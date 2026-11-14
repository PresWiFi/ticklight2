package app.ticklight.watch

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognizerIntent
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Open the app → it listens → what you say is sent to Ticklight's /api/capture.
 *
 * First run: enter the server address, then type the 6-digit code shown on the watch
 * into Ticklight (⚙ → Devices → Pair your watch). No token typing on the watch.
 */
class MainActivity : Activity() {

    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("ticklight", Context.MODE_PRIVATE) }
    private var pairing = false

    private var serverUrl: String
        get() = prefs.getString("url", "") ?: ""
        set(v) = prefs.edit().putString("url", v).apply()
    private var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        route()
    }

    override fun onDestroy() {
        pairing = false
        io.shutdownNow()
        super.onDestroy()
    }

    private fun route() {
        when {
            serverUrl.isEmpty() -> showUrlSetup()
            token.isEmpty() -> startPairing()
            else -> listen()
        }
    }

    // ---------------- capture ----------------

    private fun listen() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PROMPT, "What's on your mind?")
        }
        try {
            startActivityForResult(intent, REQ_SPEECH)
        } catch (e: ActivityNotFoundException) {
            showTextInput()  // no speech recognizer: the keyboard usually has a mic key
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_SPEECH) return
        val text = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (resultCode == RESULT_OK && !text.isNullOrBlank()) send(text) else showTextInput()
    }

    private fun showTextInput() {
        val input = EditText(this).apply {
            hint = "Type or dictate"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            imeOptions = EditorInfo.IME_ACTION_SEND
            setOnEditorActionListener { v, _, _ -> v.text.toString().takeIf { it.isNotBlank() }?.let { send(it) }; true }
        }
        screen(
            label("Ticklight", 16f, bold = true),
            input,
            button("Send") { input.text.toString().takeIf { it.isNotBlank() }?.let { send(it) } },
            button("🎙 Speak") { listen() },
            small("⚙ Reset") { resetSetup() },
        )
    }

    private fun send(text: String) {
        screen(label("Sending…", 16f), small(text))
        io.execute {
            val result = runCatching {
                http("POST", "/api/capture?source=watch&wait=1", text, "text/plain; charset=utf-8", token)
            }
            main.post {
                result.fold(
                    onSuccess = { (code, body) ->
                        when (code) {
                            in 200..299 -> showSent(body)
                            401 -> { token = ""; showError("Watch was unpaired. Pair again.") { route() } }
                            else -> showError("Server said $code") { send(text) }
                        }
                    },
                    onFailure = { showError("Can't reach Ticklight") { send(text) } },
                )
            }
        }
    }

    private fun showSent(body: String) {
        val titles = runCatching {
            val arr = JSONObject(body).getJSONArray("tasks")
            (0 until arr.length()).map { arr.getJSONObject(it).getString("title") }
        }.getOrDefault(emptyList())
        val summary = if (titles.isEmpty()) "Captured" else titles.joinToString("\n") { "• $it" }
        screen(
            label("✓", 34f, color = Color.parseColor("#3fbf7f")),
            small(summary),
            button("🎙 Another") { listen() },
        )
        main.postDelayed({ if (!isFinishing) finish() }, 4000)
    }

    // ---------------- setup ----------------

    private fun showUrlSetup() {
        val input = EditText(this).apply {
            hint = "tasks.example.com"
            setText(BuildConfig.DEFAULT_URL.ifEmpty { "https://" })
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        screen(
            label("Ticklight server", 15f, bold = true),
            input,
            button("Next") {
                var url = input.text.toString().trim().trimEnd('/')
                if (url.isNotEmpty() && !url.startsWith("http")) url = "https://$url"
                if (url.length > 8) {
                    serverUrl = url
                    startPairing()
                }
            },
        )
    }

    private fun startPairing() {
        screen(label("Connecting…", 15f))
        io.execute {
            val result = runCatching { http("POST", "/api/pair/start", "{}", "application/json", null) }
            main.post {
                val (code, body) = result.getOrElse {
                    return@post showError("Can't reach $serverUrl") { route() }
                }
                if (code !in 200..299) return@post showError("Server said $code") { route() }
                val json = JSONObject(body)
                showPairCode(json.getString("code"), json.getString("secret"))
            }
        }
    }

    private fun showPairCode(code: String, secret: String) {
        screen(
            small("In Ticklight: ⚙ → Devices → Pair your watch"),
            label(code.chunked(3).joinToString(" "), 30f, bold = true),
            small("Waiting…"),
            small("⚙ Change server") { resetSetup() },
        )
        pairing = true
        pollPairing(code, secret)
    }

    private fun pollPairing(code: String, secret: String) {
        if (!pairing) return
        io.execute {
            val payload = JSONObject().put("code", code).put("secret", secret).toString()
            val result = runCatching { http("POST", "/api/pair/poll", payload, "application/json", null) }
            main.post {
                if (!pairing) return@post
                val (status, body) = result.getOrElse {
                    main.postDelayed({ pollPairing(code, secret) }, 3000)
                    return@post
                }
                when {
                    status == 400 -> { pairing = false; startPairing() }  // expired: new code
                    status in 200..299 && JSONObject(body).has("token") -> {
                        pairing = false
                        token = JSONObject(body).getString("token")
                        screen(label("✓ Paired", 20f, color = Color.parseColor("#3fbf7f")))
                        main.postDelayed({ listen() }, 800)
                    }
                    else -> main.postDelayed({ pollPairing(code, secret) }, 3000)
                }
            }
        }
    }

    private fun resetSetup() {
        pairing = false
        token = ""
        serverUrl = ""
        showUrlSetup()
    }

    private fun showError(message: String, retry: () -> Unit) {
        screen(
            label("⚠", 26f, color = Color.parseColor("#f0b429")),
            small(message),
            button("Try again") { retry() },
            small("⚙ Reset") { resetSetup() },
        )
    }

    // ---------------- networking ----------------

    /** Returns (status code, body). Throws on network errors. */
    private fun http(method: String, path: String, body: String, type: String, bearer: String?): Pair<Int, String> {
        val conn = URL(serverUrl + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 10_000
            conn.readTimeout = 60_000  // AI sorting can take a while
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", type)
            if (bearer != null) conn.setRequestProperty("Authorization", "Bearer $bearer")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code < 400) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            return code to text
        } finally {
            conn.disconnect()
        }
    }

    // ---------------- tiny view helpers ----------------

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun screen(vararg views: View) {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            // Round screens: generous padding keeps content inside the circle
            setPadding(dp(22), dp(28), dp(22), dp(36))
            views.forEach { v ->
                addView(v, LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = dp(6) })
            }
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK)
            isFillViewport = true
            addView(column)
        })
    }

    private fun label(text: String, size: Float, bold: Boolean = false, color: Int = Color.WHITE) =
        TextView(this).apply {
            this.text = text
            textSize = size
            gravity = Gravity.CENTER
            setTextColor(color)
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }

    private fun small(text: String, onClick: (() -> Unit)? = null) =
        label(text, 13f, color = Color.parseColor("#8a91a1")).apply {
            if (onClick != null) {
                setOnClickListener { onClick() }
                setPadding(0, dp(8), 0, dp(8))
            }
        }

    private fun button(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    companion object {
        private const val REQ_SPEECH = 1
    }
}
