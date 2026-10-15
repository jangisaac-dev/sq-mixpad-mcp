// sqbridge — injected into SQ MixPad via DYLD_INSERT_LIBRARIES.
// Listens on a Unix socket readable only by the current user ($SQBRIDGE_SOCKET, default ~/.sqbridge.sock).
// No code evaluation: only typed object inspection, property get/set and public-method calls.
// One JSON request per line, one JSON line back: {"ok":true,"result":...} or {"ok":false,"error":"..."}.
//   {"op":"root"}                                   id of the main window
//   {"op":"singleton","uri":"M","type":"T"}         id of a QML singleton (e.g. ConnectionInterfaceSingleton / ConnectionInterface)
//   {"op":"inspect","id":N}                         class, objectName, parent, children, properties, methods
//   {"op":"find","id":N,"class":"..","name":"..","limit":50}   recursive child search (substring match)
//   {"op":"get","id":N,"prop":"p"}                  one property
//   {"op":"set","id":N,"prop":"p","value":v}        write property, returns the value read back
//   {"op":"call","id":N,"method":"m","args":[..]}   call a public slot / Q_INVOKABLE listed by inspect;
//                                                   give the full signature ("get(int)") to pick an overload
//   {"op":"qrc"} / {"op":"read","path":":/x"}       list / read embedded resource files
// QObject-valued properties come back as {"id":M,"class":"..."}; pass such a ref as a set value or call
// argument to hand over the object itself. Ids start at pid*1e6, so ids from an earlier app process are
// rejected instead of addressing some other object.
#include <QtCore/QtCore>
#include <QtGui/QGuiApplication>
#include <QtGui/QWindow>
#include <QtNetwork/QLocalServer>
#include <QtNetwork/QLocalSocket>
#include <QtQml/QQmlEngine>

static QHash<qint64, QPointer<QObject>> objs;
static QHash<QObject *, qint64> ids;

static qint64 idOf(QObject *o) {
    if (auto it = ids.constFind(o); it != ids.constEnd() && objs.value(*it)) return *it;
    const qint64 id = QCoreApplication::applicationPid() * 1000000LL + objs.size() + 1;
    objs.insert(id, o);
    ids.insert(o, id);
    return id;
}

static QJsonObject ref(QObject *o) {
    if (!o) return {};
    QJsonObject r{{"id", idOf(o)}, {"class", o->metaObject()->className()}};
    if (!o->objectName().isEmpty()) r["name"] = o->objectName();
    return r;
}

static QJsonValue toJson(const QVariant &v) {
    if (v.metaType().flags() & QMetaType::PointerToQObject) return ref(v.value<QObject *>());
    if (v.canConvert<QObject *>() && v.value<QObject *>()) return ref(v.value<QObject *>());
    const QJsonValue j = QJsonValue::fromVariant(v);
    if (j.isNull() && v.isValid() && !v.isNull()) return QString("<%1>").arg(v.typeName());
    return j;
}

static QVariant fromJson(const QJsonValue &j) {
    const QJsonObject r = j.toObject();
    if (r.contains("id")) {  // an unknown id gives an invalid QVariant, which set/call refuse
        QObject *o = objs.value(r.value("id").toInteger());
        return o ? QVariant::fromValue(o) : QVariant();
    }
    return j.toVariant();
}

static QJsonObject inspect(QObject *o) {
    QJsonObject r = ref(o);
    r["parent"] = ref(o->parent());
    QJsonArray kids;
    for (QObject *c : o->children()) kids.append(ref(c));
    r["children"] = kids;
    QJsonObject props;
    const QMetaObject *mo = o->metaObject();
    for (int i = 0; i < mo->propertyCount(); ++i) {
        const QMetaProperty p = mo->property(i);
        props[p.name()] = toJson(p.read(o));
    }
    r["properties"] = props;
    QJsonArray methods;  // slots / Q_INVOKABLEs declared below QObject
    for (int i = QObject::staticMetaObject.methodCount(); i < mo->methodCount(); ++i) {
        const QMetaMethod m = mo->method(i);
        if (m.access() == QMetaMethod::Public && m.methodType() != QMetaMethod::Signal)
            methods.append(QString("%1 %2").arg(m.typeName(), m.methodSignature()));
    }
    r["methods"] = methods;
    return r;
}

static QJsonObject invoke(QObject *o, const QByteArray &name, const QJsonArray &args) {
    const QMetaObject *mo = o->metaObject();
    for (int i = mo->methodCount() - 1; i >= 0; --i) {
        const QMetaMethod m = mo->method(i);
        if ((name.contains('(') ? m.methodSignature() : m.name()) != name || m.parameterCount() != args.size() || args.size() > 10 ||
            m.access() != QMetaMethod::Public || m.methodType() == QMetaMethod::Signal)
            continue;
        QVariant vals[10];
        QGenericArgument ga[10];
        for (int a = 0; a < args.size(); ++a) {
            vals[a] = fromJson(args[a]);
            if (!vals[a].convert(m.parameterMetaType(a)))
                return {{"ok", false}, {"error", QString("arg %1 not convertible to %2").arg(a).arg(m.parameterTypeName(a))}};
            ga[a] = QGenericArgument(m.parameterMetaType(a).name(), vals[a].constData());
        }
        const bool isVoid = m.returnMetaType().id() == QMetaType::Void;
        QVariant ret = isVoid ? QVariant() : QVariant(m.returnMetaType());
        QGenericReturnArgument gr = isVoid ? QGenericReturnArgument() : QGenericReturnArgument(m.typeName(), ret.data());
        if (!m.invoke(o, Qt::DirectConnection, gr, ga[0], ga[1], ga[2], ga[3], ga[4], ga[5], ga[6], ga[7], ga[8], ga[9]))
            return {{"ok", false}, {"error", "invoke failed"}};
        return {{"ok", true}, {"result", toJson(ret)}};
    }
    return {{"ok", false}, {"error", "no public method " + name + " taking " + QString::number(args.size()) + " args"}};
}

static void find(QObject *o, const QString &cls, const QString &name, int limit, QJsonArray &out) {
    for (QObject *c : o->children()) {
        if (out.size() >= limit) return;
        if ((cls.isEmpty() || QString(c->metaObject()->className()).contains(cls)) &&
            (name.isEmpty() || c->objectName().contains(name)))
            out.append(ref(c));
        find(c, cls, name, limit, out);
    }
}

static QJsonObject handle(const QJsonObject &req) {
    auto fail = [](const QString &e) { return QJsonObject{{"ok", false}, {"error", e}}; };
    auto ok = [](const QJsonValue &v) { return QJsonObject{{"ok", true}, {"result", v}}; };
    const QString op = req.value("op").toString();
    if (op == "qrc") {
        QJsonArray files;
        QDirIterator it(":/", QDir::Files, QDirIterator::Subdirectories);
        while (it.hasNext()) files.append(it.next());
        return ok(files);
    }
    if (op == "read") {
        QFile f(req.value("path").toString());
        if (!f.open(QIODevice::ReadOnly)) return fail("cannot open " + f.fileName());
        return ok(QString::fromUtf8(f.readAll()));
    }
    if (op == "root" || op == "singleton") {
        for (QWindow *w : QGuiApplication::topLevelWindows()) {
            if (!w->isVisible() || !qmlEngine(w)) continue;
            if (op == "root") return ok(ref(w));
            QObject *s = qmlEngine(w)->singletonInstance<QObject *>(req.value("uri").toString(),
                                                                    req.value("type").toString());
            return s ? ok(ref(s)) : fail("no such QObject singleton");
        }
        return fail("no visible QML window");
    }
    QObject *o = objs.value(req.value("id").toInteger());
    if (!o) return fail("unknown or deleted id");
    if (op == "inspect") return ok(inspect(o));
    if (op == "find") {
        QJsonArray out;
        find(o, req.value("class").toString(), req.value("name").toString(), req.value("limit").toInt(50), out);
        return ok(out);
    }
    if (op == "call") return invoke(o, req.value("method").toString().toUtf8(), req.value("args").toArray());
    const QByteArray prop = req.value("prop").toString().toUtf8();
    if (o->metaObject()->indexOfProperty(prop) < 0) return fail("no property " + prop);
    if (op == "get") return ok(toJson(o->property(prop)));
    if (op == "set") {
        const QVariant v = fromJson(req.value("value"));
        if (!v.isValid()) return fail("value refers to an unknown or deleted id");
        if (!o->setProperty(prop, v)) return fail("setProperty refused");
        return ok(toJson(o->property(prop)));
    }
    return fail("unknown op");
}

static void start() {
    const QString path = qEnvironmentVariable("SQBRIDGE_SOCKET", QDir::homePath() + "/.sqbridge.sock");
    auto *srv = new QLocalServer(qApp);
    srv->setSocketOptions(QLocalServer::UserAccessOption);
    QLocalServer::removeServer(path);
    if (!srv->listen(path)) {
        qWarning("sqbridge: listen on %s failed: %s", qPrintable(path), qPrintable(srv->errorString()));
        return;
    }
    qWarning("sqbridge: listening on %s", qPrintable(path));
    QObject::connect(srv, &QLocalServer::newConnection, srv, [srv] {
        while (QLocalSocket *s = srv->nextPendingConnection()) {
            QObject::connect(s, &QLocalSocket::readyRead, s, [s] {
                while (s->canReadLine()) {
                    QJsonParseError pe;
                    const QJsonDocument req = QJsonDocument::fromJson(s->readLine(), &pe);
                    const QJsonObject res = pe.error ? QJsonObject{{"ok", false}, {"error", pe.errorString()}}
                                                     : handle(req.object());
                    s->write(QJsonDocument(res).toJson(QJsonDocument::Compact) + "\n");
                }
            });
            QObject::connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
        }
    });
}

static void startup() { QTimer::singleShot(0, qApp, start); }
Q_COREAPP_STARTUP_FUNCTION(startup)
