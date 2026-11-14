package app.ticklight.phone

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.text.InputType
import android.view.Gravity
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/** Capture without opening the full app: share sheet, launcher shortcuts, widget, quick settings tile. */
class QuickAddActivity : Activity() {
    companion object {
        const val ACTION_QUICK = "app.ticklight.phone.QUICK_ADD"
        const val ACTION_VOICE = "app.ticklight.phone.VOICE_ADD"
        private const val SPEECH = 1
    }

    private lateinit var input: EditText
    private lateinit var add: Button
    private lateinit var status: TextView
    private val api by lazy { Api(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = Store(this)
        if (!store.configured || store.token.isEmpty()) {
            Toast.makeText(this, "Open Ticklight once to sign in", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return finish()
        }
        if (intent.action == Intent.ACTION_SEND) {
            val text = listOfNotNull(intent.getStringExtra(Intent.EXTRA_SUBJECT), intent.getStringExtra(Intent.EXTRA_TEXT))
                .filter { it.isNotBlank() }.distinct().joinToString("\n")
            if (text.isBlank()) return finish()
            buildUi()
            input.setText(text)
            return send(text)
        }
        buildUi()
        if (savedInstanceState == null && intent.action == ACTION_VOICE) listen()
    }

    private fun buildUi() {
        val pad = (16 * resources.displayMetrics.density).toInt()
        input = EditText(this).apply {
            hint = "Dump anything…"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 2
            maxLines = 8
            imeOptions = EditorInfo.IME_ACTION_DONE
            setOnEditorActionListener { _, id, _ -> if (id == EditorInfo.IME_ACTION_DONE) { submit(); true } else false }
        }
        status = TextView(this).apply { setPadding(0, pad / 2, 0, 0) }
        val mic = Button(this).apply { text = "🎤"; setOnClickListener { listen() } }
        add = Button(this).apply { text = "Add"; setOnClickListener { submit() } }
        val row = LinearLayout(this).apply {
            gravity = Gravity.END
            addView(mic)
            addView(add)
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad / 2)
            addView(TextView(this@QuickAddActivity).apply { text = "Add to Ticklight"; textSize = 18f; setPadding(0, 0, 0, pad / 2) })
            addView(input)
            addView(status)
            addView(row)
        })
        input.requestFocus()
    }

    private fun listen() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            status.text = "No speech recognizer on this phone; use the keyboard's mic."
            return
        }
        val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "What's on your mind?")
        runCatching { startActivityForResult(i, SPEECH) }
            .onFailure { status.text = "Voice input isn't available." }
    }

    @Deprecated("Activity API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != SPEECH || resultCode != RESULT_OK) return
        val said = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull().orEmpty()
        if (said.isBlank()) return
        val before = input.text.toString().trim()
        val text = if (before.isEmpty()) said else "$before\n$said"
        input.setText(text)
        send(text)  // speaking is the "submit"
    }

    private fun submit() {
        val text = input.text.toString().trim()
        if (text.isNotEmpty()) send(text)
    }

    private fun send(text: String) {
        add.isEnabled = false
        status.text = "Adding…"
        val app = applicationContext
        Api.bg({ api.capture(text) }) { r ->
            r.onSuccess {
                Toast.makeText(app, "➕ Added. Sorting it now.", Toast.LENGTH_SHORT).show()
                // AI sorting takes a few seconds; refresh the widget after
                Handler(Looper.getMainLooper()).postDelayed({ FocusWidget.refresh(app) }, 10_000)
                MainActivity.live?.reloadBoard()
                finish()
            }
            r.onFailure {
                add.isEnabled = true
                status.text = "Not added: ${it.message}"
            }
        }
    }
}
