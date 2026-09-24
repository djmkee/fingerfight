package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.switchmaterial.SwitchMaterial

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        findViewById<MaterialButton>(R.id.btn_play).setOnClickListener {
            startActivity(Intent(this, SetupActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.btn_how_to_play).setOnClickListener {
            startActivity(Intent(this, HowToPlayActivity::class.java))
        }

        val switchSound = findViewById<SwitchMaterial>(R.id.switch_sound)
        val switchHaptics = findViewById<SwitchMaterial>(R.id.switch_haptics)
        switchSound.isChecked = Prefs.isSoundEnabled(this)
        switchHaptics.isChecked = Prefs.isHapticsEnabled(this)
        switchSound.setOnCheckedChangeListener { _, isChecked -> Prefs.setSoundEnabled(this, isChecked) }
        switchHaptics.setOnCheckedChangeListener { _, isChecked -> Prefs.setHapticsEnabled(this, isChecked) }
    }
}
