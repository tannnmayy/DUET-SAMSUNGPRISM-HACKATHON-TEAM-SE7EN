package com.se7en.duet;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

/**
 * A visible foreground service of type microphone: while it runs, Android keeps DUET's
 * microphone and conversation alive when another app (Google Maps in Drive mode) is in front.
 * Started from DuetPhonePlugin.listenInBackground when a conversation starts, stopped when it ends.
 * Android 14+ lets it start only while DUET is on screen, so a rejoin behind Maps keeps the
 * running one instead of starting it again (running says which).
 */
public class DuetListenService extends Service {

    private static final String CHANNEL = "duet.listening";
    private static final int ID = 7;

    static volatile boolean running = false;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm != null && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "DUET listening", NotificationManager.IMPORTANCE_LOW));
        }
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("DUET is listening")
            .setContentText("Talk any time. Tap to open DUET.")
            .setOngoing(true)
            .setContentIntent(pi)
            .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(ID, n);
        }
        running = true;
        return START_NOT_STICKY;
    }

    /** DUET swiped away from the recent apps: the conversation is gone, so is the "listening" notice. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        stopSelf();
    }

    @Override
    public void onDestroy() {
        running = false;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
