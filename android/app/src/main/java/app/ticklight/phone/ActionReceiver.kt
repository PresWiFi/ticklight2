package app.ticklight.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.widget.Toast
import org.json.JSONObject

/** Notification buttons and widget taps: do the thing without opening the app. */
class ActionReceiver : BroadcastReceiver() {
    companion object {
        const val NOTIFICATION_ACTION = "app.ticklight.phone.NOTIFICATION_ACTION"
        const val COMPLETE = "app.ticklight.phone.COMPLETE"
        const val REFRESH = "app.ticklight.phone.REFRESH"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        val app = ctx.applicationContext
        val pending = goAsync()
        val api = Api(app)
        when (intent.action) {
            NOTIFICATION_ACTION -> {
                val id = intent.getIntExtra("id", 0)
                Notifier.cancel(app, id)
                Api.bg({ JSONObject(api.post("/api/action", JSONObject().put("action", intent.getStringExtra("action")))) }) { r ->
                    r.onSuccess { Notifier.ack(app, it.optString("message").ifEmpty { "Done" }) }
                    r.onFailure {
                        // Couldn't do it in the background; open the app instead
                        runCatching { Notifier.openApp(app, intent.getStringExtra("path") ?: "/", id).send() }
                    }
                    FocusWidget.refresh(app)
                    MainActivity.live?.reloadBoard()
                    pending.finish()
                }
            }
            COMPLETE -> {
                val task = intent.getIntExtra("task", 0)
                Api.bg({ JSONObject(api.post("/api/tasks/$task/complete")) }) { r ->
                    r.onSuccess {
                        val xp = it.optJSONObject("reward")?.optInt("xp") ?: 0
                        Toast.makeText(app, "✓ ${it.optString("title")}" + (if (xp > 0) "  +$xp XP" else ""), Toast.LENGTH_SHORT).show()
                    }
                    r.onFailure { Toast.makeText(app, it.message ?: "Couldn't reach Ticklight", Toast.LENGTH_SHORT).show() }
                    FocusWidget.refresh(app) { pending.finish() }
                    MainActivity.live?.reloadBoard()
                }
            }
            else -> FocusWidget.refresh(app) { pending.finish() }
        }
    }
}
