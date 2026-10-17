package com.se7en.duet;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Calendar;

/**
 * DUET's own alarms: the fallback when the phone's Clock app does not take a cancel request from
 * another app. Set, cancel and list are exact, because DUET keeps the list itself, and they work while
 * another app is on screen (no screen is opened). The alarm shows as the phone's next alarm on the
 * lock screen, and rings as an insistent alarm-sound notification with a Dismiss button.
 */
public class DuetAlarms extends BroadcastReceiver {

    static final String PREFS = "duet.alarms";
    static final String CHANNEL = "duet.alarms.ring";
    static final String RING = "com.se7en.duet.ALARM_RING";
    static final String DISMISS = "com.se7en.duet.ALARM_DISMISS";

    private static int code(int hour, int minute) { return 7000 + hour * 100 + minute; }

    private static PendingIntent ringIntent(Context c, int hour, int minute, String label) {
        Intent i = new Intent(c, DuetAlarms.class).setAction(RING)
            .putExtra("hour", hour).putExtra("minute", minute).putExtra("label", label);
        return PendingIntent.getBroadcast(c, code(hour, minute), i,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    static JSONArray load(Context c) {
        try {
            return new JSONArray(c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("alarms", "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static void save(Context c, JSONArray list) {
        SharedPreferences p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        p.edit().putString("alarms", list.toString()).apply();
    }

    /** Can this phone schedule exact alarms for DUET? (Android 12+ asks the user, or USE_EXACT_ALARM grants it.) */
    static boolean allowed(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        return am != null && (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms());
    }

    static boolean canRing(Context c) {
        return NotificationManagerCompat.from(c).areNotificationsEnabled();
    }

    /** The next time hour:minute comes round, in millis. */
    static long next(int hour, int minute) {
        Calendar now = Calendar.getInstance();
        Calendar at = (Calendar) now.clone();
        at.set(Calendar.HOUR_OF_DAY, hour);
        at.set(Calendar.MINUTE, minute);
        at.set(Calendar.SECOND, 0);
        at.set(Calendar.MILLISECOND, 0);
        if (!at.after(now)) at.add(Calendar.DAY_OF_MONTH, 1);
        return at.getTimeInMillis();
    }

    /** Sets (or replaces) the alarm at hour:minute. Returns its trigger time. */
    static long set(Context c, int hour, int minute, String label) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        long at = next(hour, minute);
        Intent show = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent showPi = PendingIntent.getActivity(c, 6999, show, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        am.setAlarmClock(new AlarmManager.AlarmClockInfo(at, showPi), ringIntent(c, hour, minute, label));
        JSONArray list = load(c), out = new JSONArray();
        try {
            for (int k = 0; k < list.length(); k++) {
                JSONObject a = list.getJSONObject(k);
                if (a.getInt("hour") != hour || a.getInt("minute") != minute) out.put(a);
            }
            out.put(new JSONObject().put("hour", hour).put("minute", minute).put("label", label == null ? "" : label).put("at", at));
        } catch (org.json.JSONException e) {
            throw new IllegalStateException("the alarm list could not be saved", e);
        }
        save(c, out);
        return at;
    }

    /** Cancels the alarm at hour:minute; false when DUET had no such alarm. */
    static boolean cancel(Context c, int hour, int minute) {
        JSONArray list = load(c), out = new JSONArray();
        boolean found = false;
        for (int k = 0; k < list.length(); k++) {
            JSONObject a = list.optJSONObject(k);
            if (a == null) continue;
            if (a.optInt("hour", -1) == hour && a.optInt("minute", -1) == minute) found = true;
            else out.put(a);
        }
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        PendingIntent pi = ringIntent(c, hour, minute, "");
        am.cancel(pi);
        pi.cancel();
        save(c, out);
        return found;
    }

    // --- ringing ---------------------------------------------------------------------------------

    @Override
    public void onReceive(Context c, Intent intent) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        int hour = intent.getIntExtra("hour", 0), minute = intent.getIntExtra("minute", 0);
        int id = code(hour, minute);
        if (DISMISS.equals(intent.getAction())) {
            nm.cancel(id);
            return;
        }
        if (!RING.equals(intent.getAction())) return;
        cancel(c, hour, minute);   // a one-time alarm: it has rung, so it is no longer set
        Uri sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        if (sound == null) sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "DUET alarms", NotificationManager.IMPORTANCE_HIGH);
            ch.setSound(sound, new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            ch.enableVibration(true);
            ch.setBypassDnd(true);
            nm.createNotificationChannel(ch);
        }
        Intent dismiss = new Intent(c, DuetAlarms.class).setAction(DISMISS).putExtra("hour", hour).putExtra("minute", minute);
        PendingIntent dismissPi = PendingIntent.getBroadcast(c, id + 1000, dismiss,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent open = PendingIntent.getActivity(c, id, new Intent(c, MainActivity.class),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        String label = intent.getStringExtra("label");
        Notification n = new NotificationCompat.Builder(c, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(String.format(java.util.Locale.ENGLISH, "Alarm · %d:%02d %s",
                (hour + 11) % 12 + 1, minute, hour < 12 ? "AM" : "PM"))
            .setContentText(label == null || label.isEmpty() ? "Set by DUET" : label)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setSound(sound)
            .setContentIntent(open)
            .setAutoCancel(true)
            .addAction(0, "Dismiss", dismissPi)
            .build();
        n.flags |= Notification.FLAG_INSISTENT;   // keeps ringing until dismissed
        nm.notify(id, n);
    }
}
