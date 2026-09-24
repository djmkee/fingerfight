package com.devmikeepr.fingerfight

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton

class HowToPlayActivity : AppCompatActivity() {

    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_how_to_play)
        glowBackground = findViewById(R.id.glow_background)
        findViewById<MaterialButton>(R.id.btn_close).setOnClickListener { finish() }
        findViewById<AdView>(R.id.ad_view).loadStandardAd()
    }

    override fun onResume() {
        super.onResume()
        glowBackground.startAnimating()
    }

    override fun onPause() {
        super.onPause()
        glowBackground.stopAnimating()
    }
}
