package app.ticklight.phone

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class ApiError(val code: Int, message: String) : IOException(message)

/** Blocking calls to the Ticklight server with the device token. Run them via [bg]. */
class Api(context: Context) {
    val store = Store(context)

    fun call(method: String, path: String, body: String? = null, contentType: String = "application/json"): String {
        if (store.token.isEmpty()) throw ApiError(401, "Open Ticklight once to sign in")
        val c = URL(store.url + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 10_000
            c.readTimeout = 60_000
            c.setRequestProperty("Authorization", "Bearer ${store.token}")
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "$contentType; charset=utf-8")
                c.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = c.responseCode
            val text = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code == 401) store.token = ""  // revoked; the app makes a new one next time it opens
            if (code >= 400) {
                val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                throw ApiError(code, detail?.takeIf { it.isNotEmpty() } ?: "Server said $code")
            }
            return text
        } finally {
            c.disconnect()
        }
    }

    fun post(path: String, json: JSONObject = JSONObject()) = call("POST", path, json.toString())
    fun get(path: String) = JSONObject(call("GET", path))

    fun capture(text: String, source: String = "android") =
        JSONObject(call("POST", "/api/capture?source=$source", text, "text/plain"))

    /** Name may carry a price and a link; the server splits them out. */
    fun wish(text: String) = JSONObject(post("/api/wishes", JSONObject().put("title", text)))

    fun subscribePush() {
        if (store.endpoint.isEmpty()) return
        val sub = JSONObject()
            .put("endpoint", store.endpoint)
            .put("keys", JSONObject().put("p256dh", store.p256dh).put("auth", store.auth))
        post("/api/push/subscribe", JSONObject().put("subscription", sub).put("label", "Android app"))
    }

    companion object {
        private val pool = Executors.newFixedThreadPool(3)
        private val main = Handler(Looper.getMainLooper())

        /** Run [work] off the main thread, then [done] on it with the result or the error. */
        fun <T> bg(work: () -> T, done: (Result<T>) -> Unit = {}) {
            pool.execute {
                val r = runCatching(work)
                main.post { done(r) }
            }
        }
    }
}
