package app.ticklight.phone

import android.content.Context

/** Server address, device token and push state. */
class Store(context: Context) {
    private val p = context.applicationContext.getSharedPreferences("ticklight", Context.MODE_PRIVATE)

    var url: String
        get() = p.getString("url", null) ?: BuildConfig.DEFAULT_URL.trimEnd('/')
        set(v) = p.edit().putString("url", v.trim().trimEnd('/')).apply()

    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) = p.edit().putString("token", v).apply()

    var uid: Int
        get() = p.getInt("uid", 0)
        set(v) = p.edit().putInt("uid", v).apply()

    /** UnifiedPush endpoint we last sent to the server, plus its keys. */
    var endpoint: String
        get() = p.getString("endpoint", "") ?: ""
        set(v) = p.edit().putString("endpoint", v).apply()
    var p256dh: String
        get() = p.getString("p256dh", "") ?: ""
        set(v) = p.edit().putString("p256dh", v).apply()
    var auth: String
        get() = p.getString("auth", "") ?: ""
        set(v) = p.edit().putString("auth", v).apply()

    /** off | pending | on | no-distributor | failed */
    var push: String
        get() = p.getString("push", "off") ?: "off"
        set(v) = p.edit().putString("push", v).apply()

    var pushError: String
        get() = p.getString("push_error", "") ?: ""
        set(v) = p.edit().putString("push_error", v).apply()

    val configured get() = url.startsWith("http")
}
