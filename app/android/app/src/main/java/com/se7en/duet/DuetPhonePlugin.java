package com.se7en.duet;

import android.Manifest;
import android.app.AlarmManager;
import android.app.AppOpsManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.provider.CalendarContract;
import android.provider.ContactsContract;
import android.view.KeyEvent;
import androidx.core.content.ContextCompat;
import java.util.TimeZone;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.content.res.Configuration;
import android.location.LocationManager;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.Build;
import android.os.PowerManager;
import android.os.Process;
import android.provider.AlarmClock;
import android.provider.Settings;
import android.telephony.SmsManager;
import android.view.Display;
import android.view.WindowManager;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The phone's hands for DUET: every real action the voice agent can take on a Galaxy,
 * through Android's public interfaces only (no root, no hidden APIs). Called from
 * www/phone.js, which DUET reaches over LiveKit RPC after its coordinator has let
 * the call through.
 */
@CapacitorPlugin(
    name = "DuetPhone",
    permissions = {
        @Permission(alias = "call", strings = { Manifest.permission.CALL_PHONE }),
        @Permission(alias = "sms", strings = { Manifest.permission.SEND_SMS }),
        @Permission(alias = "contacts", strings = { Manifest.permission.READ_CONTACTS }),
        @Permission(alias = "calendar", strings = { Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR }),
        @Permission(alias = "notifications", strings = { Manifest.permission.POST_NOTIFICATIONS }),
    }
)
public class DuetPhonePlugin extends Plugin {

    private Context ctx() { return getContext(); }

    private void start(Intent intent) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ctx().startActivity(intent);
    }

    // ------------------------------------------------------------------ Clock

    @PluginMethod
    public void setAlarm(PluginCall call) {
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, call.getInt("hour", 7))
            .putExtra(AlarmClock.EXTRA_MINUTES, call.getInt("minute", 0))
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        String label = call.getString("label", "");
        if (label != null && !label.isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label);
        try {
            start(i);
            call.resolve(new JSObject().put("set", true));
        } catch (ActivityNotFoundException e) {
            call.reject("no Clock app on this phone accepts alarms");
        }
    }

    @PluginMethod
    public void dismissAlarm(PluginCall call) {
        int hour = call.getInt("hour", 0);
        int minute = call.getInt("minute", 0);
        Intent i = new Intent(AlarmClock.ACTION_DISMISS_ALARM)
            .putExtra(AlarmClock.EXTRA_ALARM_SEARCH_MODE, AlarmClock.ALARM_SEARCH_MODE_TIME)
            .putExtra(AlarmClock.EXTRA_HOUR, hour % 12 == 0 ? 12 : hour % 12)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_IS_PM, hour >= 12)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        try {
            start(i);
            call.resolve(new JSObject().put("requested", true));
        } catch (ActivityNotFoundException e) {
            call.resolve(new JSObject().put("requested", false));
        }
    }

    @PluginMethod
    public void setTimer(PluginCall call) {
        Intent i = new Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, call.getInt("seconds", 60))
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        String label = call.getString("label", "");
        if (label != null && !label.isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label);
        try {
            start(i);
            call.resolve(new JSObject().put("set", true));
        } catch (ActivityNotFoundException e) {
            call.reject("no Clock app on this phone accepts timers");
        }
    }

    @PluginMethod
    public void nextAlarm(PluginCall call) {
        AlarmManager am = (AlarmManager) ctx().getSystemService(Context.ALARM_SERVICE);
        AlarmManager.AlarmClockInfo info = am == null ? null : am.getNextAlarmClock();
        JSObject out = new JSObject();
        if (info != null) {
            out.put("time", new SimpleDateFormat("EEE h:mm a", Locale.ENGLISH).format(info.getTriggerTime()));
        }
        call.resolve(out);
    }

    // ------------------------------------------------------------------ battery and display

    @PluginMethod
    public void batteryReport(PluginCall call) {
        Context c = ctx();
        ContentResolver cr = c.getContentResolver();
        JSObject out = new JSObject();
        Intent b = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (b != null) {
            int level = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
            int scale = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
            int status = b.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            int plugged = b.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0);
            out.put("level", scale > 0 ? Math.round(level * 100f / scale) : level);
            out.put("charging", status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL);
            out.put("plugged", plugged == BatteryManager.BATTERY_PLUGGED_AC ? "charger"
                : plugged == BatteryManager.BATTERY_PLUGGED_USB ? "usb"
                : plugged == BatteryManager.BATTERY_PLUGGED_WIRELESS ? "wireless" : "not plugged in");
            out.put("temperature_c", b.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0);
            out.put("voltage_v", b.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0) / 1000.0);
            out.put("health", health(b.getIntExtra(BatteryManager.EXTRA_HEALTH, 0)));
            if (Build.VERSION.SDK_INT >= 34) {
                int cycles = b.getIntExtra(BatteryManager.EXTRA_CYCLE_COUNT, -1);
                if (cycles >= 0) out.put("cycle_count", cycles);
            }
        }
        BatteryManager bm = (BatteryManager) c.getSystemService(Context.BATTERY_SERVICE);
        if (bm != null) {
            int now = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW);
            if (now != Integer.MIN_VALUE && now != 0) {
                // microamperes by the spec; some phones report milliamperes
                out.put("current_ma", Math.abs(now) > 20000 ? Math.round(now / 1000f) : now);
            }
        }
        PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            out.put("power_saving", pm.isPowerSaveMode());
            if (Build.VERSION.SDK_INT >= 29) out.put("thermal_status", thermal(pm.getCurrentThermalStatus()));
        }
        try {
            int raw = Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS);
            out.put("brightness_percent", Math.round(raw * 100f / 255f));
            out.put("adaptive_brightness", Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE) == 1);
        } catch (Exception ignored) { }
        try {
            out.put("screen_timeout_s", Settings.System.getInt(cr, Settings.System.SCREEN_OFF_TIMEOUT) / 1000);
        } catch (Exception ignored) { }
        try {
            WindowManager wm = (WindowManager) c.getSystemService(Context.WINDOW_SERVICE);
            Display d = wm.getDefaultDisplay();
            out.put("refresh_rate_hz", Math.round(d.getRefreshRate()));
            float max = 0;
            for (Display.Mode m : d.getSupportedModes()) max = Math.max(max, m.getRefreshRate());
            out.put("max_refresh_rate_hz", Math.round(max));
        } catch (Exception ignored) { }
        int night = c.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK;
        out.put("dark_mode", night == Configuration.UI_MODE_NIGHT_YES);
        try {
            WifiManager wifi = (WifiManager) c.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            out.put("wifi_on", wifi != null && wifi.isWifiEnabled());
        } catch (Exception ignored) { }
        try {
            BluetoothManager btm = (BluetoothManager) c.getSystemService(Context.BLUETOOTH_SERVICE);
            BluetoothAdapter a = btm == null ? null : btm.getAdapter();
            out.put("bluetooth_on", a != null && a.isEnabled());
        } catch (Exception ignored) { }
        try {
            LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
            if (Build.VERSION.SDK_INT >= 28 && lm != null) out.put("location_on", lm.isLocationEnabled());
        } catch (Exception ignored) { }
        out.put("model", Build.MANUFACTURER + " " + Build.MODEL);
        call.resolve(out);
    }

    private static String health(int h) {
        switch (h) {
            case BatteryManager.BATTERY_HEALTH_GOOD: return "good";
            case BatteryManager.BATTERY_HEALTH_OVERHEAT: return "overheating";
            case BatteryManager.BATTERY_HEALTH_DEAD: return "dead";
            case BatteryManager.BATTERY_HEALTH_OVER_VOLTAGE: return "over voltage";
            case BatteryManager.BATTERY_HEALTH_COLD: return "cold";
            case BatteryManager.BATTERY_HEALTH_UNSPECIFIED_FAILURE: return "failure";
            default: return "unknown";
        }
    }

    private static String thermal(int t) {
        switch (t) {
            case PowerManager.THERMAL_STATUS_NONE: return "normal";
            case PowerManager.THERMAL_STATUS_LIGHT: return "light";
            case PowerManager.THERMAL_STATUS_MODERATE: return "moderate";
            case PowerManager.THERMAL_STATUS_SEVERE: return "severe";
            case PowerManager.THERMAL_STATUS_CRITICAL: return "critical";
            default: return "emergency";
        }
    }

    // ------------------------------------------------------------------ apps and screen time

    private boolean usageGranted() {
        AppOpsManager ops = (AppOpsManager) ctx().getSystemService(Context.APP_OPS_SERVICE);
        int mode = Build.VERSION.SDK_INT >= 29
            ? ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx().getPackageName())
            : ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx().getPackageName());
        return mode == AppOpsManager.MODE_ALLOWED;
    }

    private static String category(ApplicationInfo ai) {
        if (Build.VERSION.SDK_INT < 26) return null;
        switch (ai.category) {
            case ApplicationInfo.CATEGORY_GAME: return "game";
            case ApplicationInfo.CATEGORY_AUDIO: return "audio";
            case ApplicationInfo.CATEGORY_VIDEO: return "video";
            case ApplicationInfo.CATEGORY_IMAGE: return "image";
            case ApplicationInfo.CATEGORY_SOCIAL: return "social";
            case ApplicationInfo.CATEGORY_NEWS: return "news";
            case ApplicationInfo.CATEGORY_MAPS: return "maps";
            case ApplicationInfo.CATEGORY_PRODUCTIVITY: return "productivity";
            default: return null;
        }
    }

    @PluginMethod
    public void appUsage(PluginCall call) {
        if (!usageGranted()) {
            call.resolve(new JSObject().put("granted", false));
            return;
        }
        int hours = call.getInt("hours", 24);
        long end = System.currentTimeMillis();
        UsageStatsManager usm = (UsageStatsManager) ctx().getSystemService(Context.USAGE_STATS_SERVICE);
        Map<String, UsageStats> stats = usm.queryAndAggregateUsageStats(end - hours * 3600_000L, end);
        PackageManager pm = ctx().getPackageManager();
        List<UsageStats> list = new ArrayList<>(stats.values());
        list.sort((a, b) -> Long.compare(b.getTotalTimeInForeground(), a.getTotalTimeInForeground()));
        JSArray apps = new JSArray();
        for (UsageStats s : list) {
            long minutes = s.getTotalTimeInForeground() / 60000;
            if (minutes < 1 || s.getPackageName().equals(ctx().getPackageName())) continue;
            try {
                ApplicationInfo ai = pm.getApplicationInfo(s.getPackageName(), 0);
                if (pm.getLaunchIntentForPackage(s.getPackageName()) == null) continue;  // launchers, system parts
                JSObject o = new JSObject();
                o.put("app", pm.getApplicationLabel(ai).toString());
                o.put("minutes", minutes);
                String cat = category(ai);
                if (cat != null) o.put("category", cat);
                apps.put(o);
            } catch (PackageManager.NameNotFoundException ignored) { }
            if (apps.length() >= 15) break;
        }
        call.resolve(new JSObject().put("granted", true).put("apps", apps));
    }

    private List<ResolveInfo> launchable() {
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        return ctx().getPackageManager().queryIntentActivities(main, 0);
    }

    @PluginMethod
    public void installedApps(PluginCall call) {
        PackageManager pm = ctx().getPackageManager();
        Set<String> seen = new HashSet<>();
        JSArray apps = new JSArray();
        for (ResolveInfo ri : launchable()) {
            String pkg = ri.activityInfo.packageName;
            if (!seen.add(pkg) || pkg.equals(ctx().getPackageName())) continue;
            JSObject o = new JSObject();
            o.put("name", ri.loadLabel(pm).toString());
            String cat = category(ri.activityInfo.applicationInfo);
            if (cat != null) o.put("category", cat);
            apps.put(o);
        }
        call.resolve(new JSObject().put("apps", apps));
    }

    @PluginMethod
    public void openApp(PluginCall call) {
        String want = call.getString("name", "").toLowerCase(Locale.ROOT).trim();
        PackageManager pm = ctx().getPackageManager();
        ResolveInfo best = null;
        int bestScore = 0;
        for (ResolveInfo ri : launchable()) {
            String label = ri.loadLabel(pm).toString().toLowerCase(Locale.ROOT);
            int score = label.equals(want) ? 3 : label.startsWith(want) ? 2 : (label.contains(want) || want.contains(label)) ? 1 : 0;
            if (score > bestScore) { best = ri; bestScore = score; }
        }
        if (best == null) {
            call.resolve(new JSObject());
            return;
        }
        Intent i = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        if (i != null) start(i);
        call.resolve(new JSObject().put("opened", best.loadLabel(pm).toString()));
    }

    // ------------------------------------------------------------------ settings

    @PluginMethod
    public void setBrightness(PluginCall call) {
        if (!Settings.System.canWrite(ctx())) {
            call.resolve(new JSObject().put("granted", false));
            return;
        }
        ContentResolver cr = ctx().getContentResolver();
        int percent = call.getInt("percent", -1);
        Boolean adaptive = call.getBoolean("adaptive", null);
        if (adaptive != null) {
            Settings.System.putInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE, adaptive ? 1 : 0);
        }
        if (percent >= 0) {
            if (adaptive == null || !adaptive) Settings.System.putInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE, 0);
            int raw = Math.max(1, Math.min(255, Math.round(percent * 255f / 100f)));
            Settings.System.putInt(cr, Settings.System.SCREEN_BRIGHTNESS, raw);
        }
        call.resolve(new JSObject().put("granted", true));
    }

    @PluginMethod
    public void setScreenTimeout(PluginCall call) {
        if (!Settings.System.canWrite(ctx())) {
            call.resolve(new JSObject().put("granted", false));
            return;
        }
        int seconds = Math.max(15, Math.min(1800, call.getInt("seconds", 30)));
        Settings.System.putInt(ctx().getContentResolver(), Settings.System.SCREEN_OFF_TIMEOUT, seconds * 1000);
        call.resolve(new JSObject().put("granted", true));
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        String page = call.getString("page", "battery");
        Intent i;
        switch (page) {
            case "power_saving": i = new Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS); break;
            case "device_care": {
                i = ctx().getPackageManager().getLaunchIntentForPackage("com.samsung.android.lool");
                if (i == null) i = new Intent(Intent.ACTION_POWER_USAGE_SUMMARY);
                break;
            }
            case "display": case "motion_smoothness": i = new Intent(Settings.ACTION_DISPLAY_SETTINGS); break;
            case "usage_access": i = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS); break;
            case "modify_system_settings":
                i = new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:" + ctx().getPackageName()));
                break;
            case "wifi": i = new Intent(Settings.ACTION_WIFI_SETTINGS); break;
            case "bluetooth": i = new Intent(Settings.ACTION_BLUETOOTH_SETTINGS); break;
            case "location": i = new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS); break;
            case "apps": i = new Intent(Settings.ACTION_APPLICATION_SETTINGS); break;
            default: i = new Intent(Intent.ACTION_POWER_USAGE_SUMMARY);
        }
        try {
            start(i);
        } catch (ActivityNotFoundException e) {
            start(new Intent(Settings.ACTION_SETTINGS));
        }
        call.resolve(new JSObject().put("opened", page));
    }

    // ------------------------------------------------------------------ Maps, calls, texts

    @PluginMethod
    public void navigate(PluginCall call) {
        String dest = Uri.encode(call.getString("destination", ""));
        String mode = call.getString("mode", "driving");
        Map<String, String> codes = new HashMap<>();
        codes.put("driving", "d");
        codes.put("two_wheeler", "l");
        codes.put("walking", "w");
        Uri uri = codes.containsKey(mode)
            ? Uri.parse("google.navigation:q=" + dest + "&mode=" + codes.get(mode))
            : Uri.parse("https://www.google.com/maps/dir/?api=1&travelmode=transit&destination=" + dest);
        Intent i = new Intent(Intent.ACTION_VIEW, uri).setPackage("com.google.android.apps.maps");
        try {
            start(i);
        } catch (ActivityNotFoundException e) {
            start(new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination=" + dest)));
        }
        call.resolve(new JSObject().put("started", true));
    }

    @PluginMethod
    public void call(PluginCall call) {
        String number = call.getString("number", "");
        boolean direct = call.getBoolean("direct", false) && getPermissionState("call") == PermissionState.GRANTED;
        start(new Intent(direct ? Intent.ACTION_CALL : Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number))));
        call.resolve(new JSObject().put("direct", direct));
    }

    @PluginMethod
    public void sms(PluginCall call) {
        String number = call.getString("number", "");
        String text = call.getString("text", "");
        boolean direct = call.getBoolean("direct", false) && getPermissionState("sms") == PermissionState.GRANTED;
        if (direct) {
            SmsManager sm = Build.VERSION.SDK_INT >= 31 ? ctx().getSystemService(SmsManager.class) : SmsManager.getDefault();
            sm.sendMultipartTextMessage(number, null, sm.divideMessage(text), null, null);
            call.resolve(new JSObject().put("sent", true));
            return;
        }
        Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(number))).putExtra("sms_body", text);
        start(i);
        call.resolve(new JSObject().put("sent", false));
    }

    // ------------------------------------------------------------------ torch, media, volume

    @PluginMethod
    public void torch(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", false));
        try {
            CameraManager cm = (CameraManager) ctx().getSystemService(Context.CAMERA_SERVICE);
            String id = null;
            for (String cid : cm.getCameraIdList()) {
                CameraCharacteristics c = cm.getCameraCharacteristics(cid);
                Boolean flash = c.get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                Integer facing = c.get(CameraCharacteristics.LENS_FACING);
                if (Boolean.TRUE.equals(flash) && (id == null || (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK))) {
                    id = cid;
                }
            }
            if (id == null) {
                call.resolve(new JSObject().put("ok", false).put("error", "this phone has no torch"));
                return;
            }
            cm.setTorchMode(id, on);
            call.resolve(new JSObject().put("ok", true).put("on", on));
        } catch (Exception e) {   // the camera is busy, or the phone refuses: reported, never thrown
            call.resolve(new JSObject().put("ok", false).put("error", "the torch is not available: " + e.getMessage()));
        }
    }

    @PluginMethod
    public void mediaKey(PluginCall call) {
        String action = call.getString("action", "pause");
        int code;
        switch (action == null ? "" : action) {
            case "play": code = KeyEvent.KEYCODE_MEDIA_PLAY; break;
            case "next": code = KeyEvent.KEYCODE_MEDIA_NEXT; break;
            case "previous": code = KeyEvent.KEYCODE_MEDIA_PREVIOUS; break;
            default: code = KeyEvent.KEYCODE_MEDIA_PAUSE;
        }
        AudioManager am = (AudioManager) ctx().getSystemService(Context.AUDIO_SERVICE);
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, code));
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, code));
        call.resolve(new JSObject().put("sent", action));
    }

    private int musicPercent(AudioManager am) {
        int max = Math.max(1, am.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
        return Math.round(am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / max);
    }

    @PluginMethod
    public void volume(PluginCall call) {
        AudioManager am = (AudioManager) ctx().getSystemService(Context.AUDIO_SERVICE);
        call.resolve(new JSObject().put("percent", musicPercent(am)));
    }

    @PluginMethod
    public void setVolume(PluginCall call) {
        AudioManager am = (AudioManager) ctx().getSystemService(Context.AUDIO_SERVICE);
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int p = Math.max(0, Math.min(100, call.getInt("percent", 50)));
        try {
            am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(max * p / 100f), AudioManager.FLAG_SHOW_UI);
        } catch (SecurityException e) {   // Do Not Disturb can refuse volume changes
            call.resolve(new JSObject().put("percent", -1).put("error", "the phone did not allow the volume change"));
            return;
        }
        call.resolve(new JSObject().put("percent", musicPercent(am)));
    }

    // ------------------------------------------------------------------ calendar and contacts

    @PluginMethod
    public void addEvent(PluginCall call) {
        if (getPermissionState("calendar") != PermissionState.GRANTED) {
            call.resolve(new JSObject().put("granted", false));
            return;
        }
        Long start = call.getLong("start");
        Long end = call.getLong("end");
        String title = call.getString("title", "Event");
        if (start == null || end == null) {
            call.resolve(new JSObject().put("granted", true).put("added", false).put("error", "no start or end time"));
            return;
        }
        ContentResolver cr = ctx().getContentResolver();
        long calendarId = -1;
        String[] cols = { CalendarContract.Calendars._ID, CalendarContract.Calendars.IS_PRIMARY,
                          CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL };
        try (Cursor c = cr.query(CalendarContract.Calendars.CONTENT_URI, cols,
                                 CalendarContract.Calendars.VISIBLE + "=1", null, null)) {
            while (c != null && c.moveToNext()) {
                if (c.getInt(2) < CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR) continue;
                boolean primary = c.getInt(1) == 1;
                if (calendarId < 0 || primary) calendarId = c.getLong(0);
                if (primary) break;
            }
        } catch (Exception e) {
            call.resolve(new JSObject().put("granted", true).put("added", false).put("error", "calendar: " + e.getMessage()));
            return;
        }
        if (calendarId < 0) {
            call.resolve(new JSObject().put("granted", true).put("added", false)
                .put("error", "no calendar on this phone accepts new events"));
            return;
        }
        ContentValues v = new ContentValues();
        v.put(CalendarContract.Events.CALENDAR_ID, calendarId);
        v.put(CalendarContract.Events.TITLE, title);
        v.put(CalendarContract.Events.DTSTART, start);
        v.put(CalendarContract.Events.DTEND, end);
        v.put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().getID());
        Uri uri = cr.insert(CalendarContract.Events.CONTENT_URI, v);
        call.resolve(new JSObject().put("granted", true).put("added", uri != null)
            .put("id", uri == null ? "" : uri.getLastPathSegment()));
    }

    @PluginMethod
    public void findContacts(PluginCall call) {
        if (getPermissionState("contacts") != PermissionState.GRANTED) {
            call.resolve(new JSObject().put("granted", false));
            return;
        }
        String q = call.getString("query", "");
        q = q == null ? "" : q.trim();
        JSArray out = new JSArray();
        String[] cols = { ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER };
        try (Cursor c = ctx().getContentResolver().query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, cols,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " LIKE ?", new String[] { "%" + q + "%" },
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC")) {
            Set<String> seen = new HashSet<>();
            while (c != null && c.moveToNext() && out.length() < 10) {
                String name = c.getString(0), number = c.getString(1);
                if (name == null || number == null || !seen.add(name)) continue;
                out.put(new JSObject().put("name", name).put("number", number));
            }
        } catch (Exception e) {
            call.resolve(new JSObject().put("granted", true).put("contacts", out).put("error", e.getMessage()));
            return;
        }
        call.resolve(new JSObject().put("granted", true).put("contacts", out));
    }

    // ------------------------------------------------------------------ listening in the background

    /** Keeps the microphone (and DUET) alive while another app, such as Google Maps, is in front.
     *  Android allows this only as a visible foreground service started while DUET is open. */
    @PluginMethod
    public void listenInBackground(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", true));
        Intent i = new Intent(ctx(), DuetListenService.class);
        try {
            if (on) ContextCompat.startForegroundService(ctx(), i);
            else ctx().stopService(i);
            call.resolve(new JSObject().put("on", on));
        } catch (Exception e) {
            call.reject("background listening is not available: " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ permissions, audio, device

    @PluginMethod
    public void permissions(PluginCall call) {
        JSObject out = new JSObject();
        out.put("usage_access", usageGranted());
        out.put("modify_system_settings", Settings.System.canWrite(ctx()));
        out.put("call_phone", getPermissionState("call") == PermissionState.GRANTED);
        out.put("send_sms", getPermissionState("sms") == PermissionState.GRANTED);
        out.put("contacts", getPermissionState("contacts") == PermissionState.GRANTED);
        out.put("calendar", getPermissionState("calendar") == PermissionState.GRANTED);
        out.put("notifications", Build.VERSION.SDK_INT < 33 || getPermissionState("notifications") == PermissionState.GRANTED);
        call.resolve(out);
    }

    @PluginMethod
    public void requestPermission(PluginCall call) {
        String name = call.getString("name", "");
        switch (name) {
            case "usage_access": start(new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)); call.resolve(); break;
            case "modify_system_settings":
                start(new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:" + ctx().getPackageName())));
                call.resolve();
                break;
            case "call_phone": requestPermissionForAlias("call", call, "permissionDone"); break;
            case "send_sms": requestPermissionForAlias("sms", call, "permissionDone"); break;
            case "contacts": requestPermissionForAlias("contacts", call, "permissionDone"); break;
            case "calendar": requestPermissionForAlias("calendar", call, "permissionDone"); break;
            case "notifications": requestPermissionForAlias("notifications", call, "permissionDone"); break;
            default: call.reject("unknown permission " + name);
        }
    }

    @PermissionCallback
    private void permissionDone(PluginCall call) {
        call.resolve();
    }

    /** Voice out of the loudspeaker, or of the Galaxy Buds / a headset when one is connected. */
    @PluginMethod
    public void audioRoute(PluginCall call) {
        AudioManager am = (AudioManager) ctx().getSystemService(Context.AUDIO_SERVICE);
        String used = "default";
        if (am != null) {
            if (Build.VERSION.SDK_INT >= 31) {
                AudioDeviceInfo speaker = null, headset = null;
                for (AudioDeviceInfo d : am.getAvailableCommunicationDevices()) {
                    int t = d.getType();
                    if (t == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER) speaker = d;
                    if (t == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || t == AudioDeviceInfo.TYPE_BLE_HEADSET
                        || t == AudioDeviceInfo.TYPE_WIRED_HEADSET || t == AudioDeviceInfo.TYPE_USB_HEADSET) headset = d;
                }
                AudioDeviceInfo pick = headset != null ? headset : speaker;
                if (pick != null && am.setCommunicationDevice(pick)) used = headset != null ? "headset" : "speaker";
            } else {
                boolean headset = am.isBluetoothScoOn() || am.isWiredHeadsetOn();
                am.setSpeakerphoneOn(!headset);
                used = headset ? "headset" : "speaker";
            }
        }
        call.resolve(new JSObject().put("route", used));
    }

    @PluginMethod
    public void keepAwake(PluginCall call) {
        boolean on = call.getBoolean("on", true);
        getActivity().runOnUiThread(() -> {
            if (on) getActivity().getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else getActivity().getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        });
        call.resolve();
    }

    @PluginMethod
    public void deviceInfo(PluginCall call) {
        String name = null;
        try { name = Settings.Global.getString(ctx().getContentResolver(), Settings.Global.DEVICE_NAME); } catch (Exception ignored) { }
        String line = (name != null && !name.isEmpty() ? name + " (" + Build.MODEL + ")" : Build.MANUFACTURER + " " + Build.MODEL)
            + ", Android " + Build.VERSION.RELEASE;
        call.resolve(new JSObject().put("line", line).put("model", Build.MODEL).put("android", Build.VERSION.RELEASE));
    }
}
