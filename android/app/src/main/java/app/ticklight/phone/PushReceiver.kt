package app.ticklight.phone

import android.util.Log
import org.json.JSONObject
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/** Receives Web Push messages through a UnifiedPush distributor (e.g. the ntfy app). */
class PushReceiver : PushService() {
    private val store by lazy { Store(this) }

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        val keys = endpoint.pubKeySet ?: return failed("distributor gave no encryption keys")
        val old = store.endpoint
        store.endpoint = endpoint.url
        store.p256dh = keys.pubKey
        store.auth = keys.auth
        val api = Api(this)
        Api.bg({
            if (old.isNotEmpty() && old != endpoint.url) {
                runCatching { api.post("/api/push/unsubscribe", JSONObject().put("endpoint", old)) }
            }
            api.subscribePush()
        }) { r ->
            r.onSuccess { store.push = "on"; store.pushError = "" }
            r.onFailure { failed("couldn't tell the server: ${it.message}") }
            MainActivity.live?.pushChanged()
        }
    }

    override fun onMessage(message: PushMessage, instance: String) {
        if (!message.decrypted) {
            Log.w("ticklight", "dropping undecryptable push")
            return
        }
        val d = runCatching { JSONObject(String(message.content)) }
            .getOrElse { JSONObject().put("title", "Ticklight").put("body", String(message.content)) }
        Notifier.show(this, d)
        FocusWidget.refresh(applicationContext)
        MainActivity.live?.reloadBoard()
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        failed(reason.name.lowercase().replace('_', ' '))
    }

    override fun onUnregistered(instance: String) {
        val old = store.endpoint
        store.endpoint = ""
        store.push = "off"
        if (old.isNotEmpty()) {
            val api = Api(this)
            Api.bg({ api.post("/api/push/unsubscribe", JSONObject().put("endpoint", old)) })
        }
        MainActivity.live?.pushChanged()
    }

    private fun failed(why: String) {
        store.push = "failed"
        store.pushError = why
        MainActivity.live?.pushChanged()
    }
}
