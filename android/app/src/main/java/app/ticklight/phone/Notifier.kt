package app.ticklight.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/** Shows the server's push payloads ({title, body, tag, url, actions, requireInteraction}). */
object Notifier {
    const val REMINDERS = "reminders"
    const val IMPORTANT = "important"
    const val QUIET = "quiet"
    private const val ACK_ID = 1

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(listOf(
            NotificationChannel(REMINDERS, "Nudges and reminders", NotificationManager.IMPORTANCE_HIGH),
            NotificationChannel(IMPORTANT, "Important (meds, meetings, deadlines)", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "Still comes through during hyperfocus" },
            NotificationChannel(QUIET, "Confirmations", NotificationManager.IMPORTANCE_LOW),
        ))
    }

    fun openApp(ctx: Context, path: String, requestCode: Int): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_PATH, path)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(ctx, requestCode, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun show(ctx: Context, d: JSONObject) {
        channels(ctx)
        val tag = d.optString("tag").ifEmpty { "ticklight" }
        val id = tag.hashCode()
        val path = d.optString("url").ifEmpty { "/" }
        val critical = d.optBoolean("requireInteraction")
        val body = d.optString("body")
        val b = Notification.Builder(ctx, if (critical) IMPORTANT else REMINDERS)
            .setSmallIcon(R.drawable.ic_stat)
            .setColor(ctx.getColor(R.color.accent))
            .setContentTitle(d.optString("title").ifEmpty { "Ticklight" })
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .setContentIntent(openApp(ctx, path, id))
        val actions = d.optJSONArray("actions")
        for (i in 0 until minOf(3, actions?.length() ?: 0)) {
            val a = actions!!.getJSONObject(i)
            val intent = Intent(ctx, ActionReceiver::class.java)
                .setAction(ActionReceiver.NOTIFICATION_ACTION)
                .putExtra("action", a.optString("action"))
                .putExtra("id", id)
                .putExtra("path", path)
            val pi = PendingIntent.getBroadcast(ctx, id * 4 + i, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            b.addAction(Notification.Action.Builder(null, a.optString("title"), pi).build())
        }
        ctx.getSystemService(NotificationManager::class.java).notify(tag, 0, b.build())
    }

    fun cancel(ctx: Context, tagHash: Int) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.activeNotifications.filter { (it.tag ?: "").hashCode() == tagHash }.forEach { nm.cancel(it.tag, it.id) }
    }

    /** Brief confirmation after a notification button, gone after a few seconds. */
    fun ack(ctx: Context, text: String) {
        channels(ctx)
        val n = Notification.Builder(ctx, QUIET)
            .setSmallIcon(R.drawable.ic_stat)
            .setColor(ctx.getColor(R.color.accent))
            .setContentTitle(text)
            .setTimeoutAfter(4000)
            .setAutoCancel(true)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify("ack", ACK_ID, n)
    }
}
