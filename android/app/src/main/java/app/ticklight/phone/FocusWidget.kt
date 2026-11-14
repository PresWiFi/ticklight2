package app.ticklight.phone

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject

/** Home screen widget: the top 3 of Focus, tick them off right there. */
class FocusWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        val pending = goAsync()
        refresh(ctx.applicationContext) { pending.finish() }
    }

    companion object {
        private val ROWS = intArrayOf(R.id.row0, R.id.row1, R.id.row2)
        private val TITLES = intArrayOf(R.id.title0, R.id.title1, R.id.title2)
        private val DONES = intArrayOf(R.id.done0, R.id.done1, R.id.done2)

        fun refresh(ctx: Context, done: () -> Unit = {}) {
            val mgr = AppWidgetManager.getInstance(ctx)
            val ids = mgr.getAppWidgetIds(ComponentName(ctx, FocusWidget::class.java))
            if (ids.isEmpty()) return done()
            val api = Api(ctx)
            Api.bg({ api.get("/api/focus?n=3") }) { r ->
                mgr.updateAppWidget(ids, render(ctx, r))
                done()
            }
        }

        private fun render(ctx: Context, r: Result<JSONObject>): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_focus)
            v.setOnClickPendingIntent(R.id.widget_root, Notifier.openApp(ctx, "/", 9001))
            v.setOnClickPendingIntent(R.id.add, quickAdd(ctx, QuickAddActivity.ACTION_QUICK, 9002))
            v.setOnClickPendingIntent(R.id.voice, quickAdd(ctx, QuickAddActivity.ACTION_VOICE, 9003))
            val tasks = r.getOrNull()?.optJSONArray("tasks")
            val n = minOf(3, tasks?.length() ?: 0)
            v.setViewVisibility(R.id.empty, if (n == 0) View.VISIBLE else View.GONE)
            if (r.isFailure) {
                val e = r.exceptionOrNull()
                v.setTextViewText(R.id.empty, if (e is ApiError && e.code == 401) "Open Ticklight to sign in." else "Can't reach Ticklight right now.")
                v.setOnClickPendingIntent(R.id.empty, refreshIntent(ctx))
            }
            for (i in 0 until 3) {
                v.setViewVisibility(ROWS[i], if (i < n) View.VISIBLE else View.GONE)
                if (i >= n) continue
                val t = tasks!!.getJSONObject(i)
                val step = t.optJSONObject("next_step")?.optString("title")
                v.setTextViewText(TITLES[i], t.optString("title") + (step?.let { "  ↳ $it" } ?: ""))
                val complete = Intent(ctx, ActionReceiver::class.java)
                    .setAction(ActionReceiver.COMPLETE).putExtra("task", t.optInt("id"))
                v.setOnClickPendingIntent(DONES[i], PendingIntent.getBroadcast(ctx, 9100 + i, complete,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            }
            return v
        }

        private fun quickAdd(ctx: Context, action: String, code: Int): PendingIntent =
            PendingIntent.getActivity(ctx, code,
                Intent(ctx, QuickAddActivity::class.java).setAction(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        private fun refreshIntent(ctx: Context): PendingIntent =
            PendingIntent.getBroadcast(ctx, 9200, Intent(ctx, ActionReceiver::class.java).setAction(ActionReceiver.REFRESH),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
}
