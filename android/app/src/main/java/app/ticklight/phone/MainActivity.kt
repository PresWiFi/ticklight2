package app.ticklight.phone

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.inputmethod.EditorInfo
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import org.unifiedpush.android.connector.UnifiedPush
import java.net.HttpURLConnection
import java.net.URL

/** The whole Ticklight web app in its own WebView (so Chrome's screen time limit doesn't apply),
 * plus native notifications, voice permission and a bridge the page uses for app-only settings. */
class MainActivity : Activity() {
    companion object {
        const val EXTRA_PATH = "path"
        private const val REQ_MIC = 1
        private const val REQ_NOTIFY = 2
        var live: MainActivity? = null
    }

    private val store by lazy { Store(this) }
    private var web: WebView? = null
    private var pendingMic: PermissionRequest? = null
    private var pendingVapid: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifier.channels(this)
        if (store.configured) openWeb(pathFor(intent)) else showSetup()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val path = pathFor(intent)
        if (web != null && path != null) web!!.loadUrl(store.url + path)
    }

    private fun pathFor(i: Intent?): String? = when {
        i?.getStringExtra(EXTRA_PATH) != null -> i.getStringExtra(EXTRA_PATH)!!.let { if (it.startsWith("/")) it else "/$it" }
        i?.action == "app.ticklight.phone.SESSION" -> "/?session"
        else -> null
    }

    override fun onResume() {
        super.onResume()
        live = this
        reloadBoard()
    }

    override fun onPause() {
        super.onPause()
        if (live === this) live = null
        CookieManager.getInstance().flush()
        FocusWidget.refresh(applicationContext)
    }

    // ---------- first run ----------
    private fun showSetup(error: String = "") {
        web?.destroy()
        web = null
        val pad = (24 * resources.displayMetrics.density).toInt()
        val url = EditText(this).apply {
            hint = "https://ticklight.example.com"
            setText(store.url.ifEmpty { "https://" })
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_GO
            setTextColor(getColor(R.color.text))
            setHintTextColor(getColor(R.color.muted))
        }
        val msg = TextView(this).apply { text = error; setTextColor(Color.parseColor("#ff7a7a")) }
        val go = Button(this).apply { text = "Connect" }
        val connect = {
            val u = url.text.toString().trim().trimEnd('/').let { if (it.startsWith("http")) it else "https://$it" }
            go.isEnabled = false
            msg.text = "Checking…"
            Api.bg({ check(u) }) { r ->
                go.isEnabled = true
                r.onSuccess { store.url = u; openWeb(null) }
                r.onFailure { msg.text = "Couldn't reach Ticklight there: ${it.message}" }
            }
        }
        go.setOnClickListener { connect() }
        url.setOnEditorActionListener { _, id, _ -> if (id == EditorInfo.IME_ACTION_GO) { connect(); true } else false }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(TextView(this@MainActivity).apply { text = "✓ Ticklight"; textSize = 28f; setTextColor(getColor(R.color.text)) })
            addView(TextView(this@MainActivity).apply {
                text = "Your server address"; setTextColor(getColor(R.color.muted)); setPadding(0, pad, 0, 0)
            })
            addView(url)
            addView(go)
            addView(msg)
        })
    }

    private fun check(u: String) {
        val c = URL("$u/api/health").openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = 8000
        try {
            if (c.responseCode != 200) throw Exception("HTTP ${c.responseCode}")
            if (!c.inputStream.bufferedReader().readText().contains("\"ok\"")) throw Exception("that isn't a Ticklight server")
        } finally {
            c.disconnect()
        }
    }

    // ---------- web app ----------
    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    private fun openWeb(path: String?) {
        WebView.setWebContentsDebuggingEnabled(true)
        val w = WebView(this)
        web = w
        w.setBackgroundColor(getColor(R.color.bg))
        w.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            userAgentString = "$userAgentString TicklightAndroid/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().setAcceptCookie(true)
        w.addJavascriptInterface(Bridge(), "TicklightApp")
        w.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.scheme != "http" && u.scheme != "https") return openExternal(u)
                val server = Uri.parse(store.url).host
                val here = Uri.parse(view.url ?: "").host
                // Stay inside for our server and the login flow (redirects to Authentik and back)
                if (u.host == server || req.isRedirect || (here != null && here != server)) return false
                return openExternal(u)
            }
        }
        w.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(req: PermissionRequest) {
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE !in req.resources) return req.deny()
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    req.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                } else {
                    pendingMic = req
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
                }
            }
        }
        setContentView(w)
        w.loadUrl(store.url + (path ?: "/"))
    }

    private fun openExternal(u: Uri): Boolean {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: ActivityNotFoundException) {
        }
        return true
    }

    private fun js(code: String) = runOnUiThread { web?.evaluateJavascript(code, null) }

    fun reloadBoard() = js("window.ticklightResume && window.ticklightResume()")
    fun pushChanged() = js("window.ticklightPush && window.ticklightPush()")

    @Deprecated("Activity API")
    override fun onBackPressed() {
        val w = web ?: return super.onBackPressed()
        // Let the page close an open dialog first
        w.evaluateJavascript("window.ticklightBack ? window.ticklightBack() : false") { handled ->
            if (handled == "true") return@evaluateJavascript
            if (w.canGoBack()) w.goBack() else moveTaskToBack(true)
        }
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        val ok = results.isNotEmpty() && results[0] == PackageManager.PERMISSION_GRANTED
        when (code) {
            REQ_MIC -> {
                pendingMic?.let { if (ok) it.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else it.deny() }
                pendingMic = null
            }
            REQ_NOTIFY -> {
                val vapid = pendingVapid
                pendingVapid = null
                if (ok && vapid != null) registerPush(vapid) else {
                    store.push = "failed"
                    store.pushError = "notifications are blocked for Ticklight in Android settings"
                    pushChanged()
                }
            }
        }
    }

    // ---------- push ----------
    private fun enablePush(vapid: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            pendingVapid = vapid
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
            return
        }
        registerPush(vapid)
    }

    private fun registerPush(vapid: String) {
        store.push = "pending"
        store.pushError = ""
        pushChanged()
        UnifiedPush.tryUseCurrentOrDefaultDistributor(this) { ok ->
            val distributors = UnifiedPush.getDistributors(this)
            when {
                ok -> UnifiedPush.register(this, messageForDistributor = "Ticklight", vapid = vapid)
                distributors.isNotEmpty() -> {
                    UnifiedPush.saveDistributor(this, distributors.first())
                    UnifiedPush.register(this, messageForDistributor = "Ticklight", vapid = vapid)
                }
                else -> store.push = "no-distributor"
            }
            pushChanged()
        }
    }

    private fun disablePush() {
        val old = store.endpoint
        runCatching { UnifiedPush.unregister(this) }
        store.endpoint = ""
        store.push = "off"
        if (old.isNotEmpty()) {
            val api = Api(this)
            Api.bg({ api.post("/api/push/unsubscribe", JSONObject().put("endpoint", old)) })
        }
        pushChanged()
    }

    private fun resetServer() {
        disablePush()
        store.token = ""
        store.uid = 0
        CookieManager.getInstance().removeAllCookies(null)
        WebStorage.getInstance().deleteAllData()
        showSetup()
    }

    /** window.TicklightApp in the page. Calls arrive on a background thread. */
    inner class Bridge {
        @JavascriptInterface
        fun info(): String = JSONObject()
            .put("version", BuildConfig.VERSION_NAME)
            .put("token", store.token.isNotEmpty())
            .put("uid", store.uid)
            .put("push", store.push)
            .put("pushError", store.pushError)
            .put("distributor", runCatching { UnifiedPush.getAckDistributor(this@MainActivity) }.getOrNull() ?: "")
            .put("distributors", runCatching { UnifiedPush.getDistributors(this@MainActivity).size }.getOrDefault(0))
            .toString()

        @JavascriptInterface
        fun setToken(token: String, uid: Int) {
            store.token = token
            store.uid = uid
            val api = Api(this@MainActivity)
            // Re-attach this phone's push endpoint to whoever is signed in now
            Api.bg({ api.subscribePush() })
            FocusWidget.refresh(applicationContext)
        }

        @JavascriptInterface
        fun clearToken() {
            store.token = ""
            store.uid = 0
        }

        @JavascriptInterface
        fun enablePush(vapid: String) = runOnUiThread { this@MainActivity.enablePush(vapid) }

        @JavascriptInterface
        fun disablePush() = runOnUiThread { this@MainActivity.disablePush() }

        @JavascriptInterface
        fun resetServer() = runOnUiThread { this@MainActivity.resetServer() }

        @JavascriptInterface
        fun openExternal(url: String) {
            runOnUiThread { this@MainActivity.openExternal(Uri.parse(url)) }
        }

        @JavascriptInterface
        fun refreshWidget() = FocusWidget.refresh(applicationContext)
    }
}
