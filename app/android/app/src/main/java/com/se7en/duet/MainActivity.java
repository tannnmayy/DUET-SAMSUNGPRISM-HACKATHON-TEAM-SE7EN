package com.se7en.duet;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DuetPhonePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
