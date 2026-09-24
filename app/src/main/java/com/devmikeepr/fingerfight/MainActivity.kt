package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.switchmaterial.SwitchMaterial

class MainActivity : AppCompatActivity() {

    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        glowBackground = findViewById(R.id.glow_background)

        val textTitle = findViewById<View>(R.id.text_title)
        val textTagline = findViewById<View>(R.id.text_tagline)
        val cardReaction = findViewById<MaterialCardView>(R.id.card_reaction)
        val cardTapBattle = findViewById<MaterialCardView>(R.id.card_tap_battle)
        val cardEndurance = findViewById<MaterialCardView>(R.id.card_endurance)
        val cardPicker = findViewById<MaterialCardView>(R.id.card_picker)
        val btnHowToPlay = findViewById<MaterialButton>(R.id.btn_how_to_play)
        val rowSound = findViewById<View>(R.id.row_sound)
        val rowHaptics = findViewById<View>(R.id.row_haptics)
        val iconSound = findViewById<ImageView>(R.id.icon_sound)
        val switchSound = findViewById<SwitchMaterial>(R.id.switch_sound)
        val switchHaptics = findViewById<SwitchMaterial>(R.id.switch_haptics)

        cardReaction.setOnClickListener { openDuelSetup(DuelMode.REACTION) }
        cardTapBattle.setOnClickListener { openDuelSetup(DuelMode.TAP_BATTLE) }
        cardEndurance.setOnClickListener { openDuelSetup(DuelMode.ENDURANCE) }
        cardPicker.setOnClickListener {
            startActivity(Intent(this, PickerSetupActivity::class.java))
        }
        btnHowToPlay.setOnClickListener {
            startActivity(Intent(this, HowToPlayActivity::class.java))
        }

        cardReaction.applyPressAnimation()
        cardTapBattle.applyPressAnimation()
        cardEndurance.applyPressAnimation()
        cardPicker.applyPressAnimation()
        btnHowToPlay.applyPressAnimation()

        switchSound.isChecked = Prefs.isSoundEnabled(this)
        switchHaptics.isChecked = Prefs.isHapticsEnabled(this)
        iconSound.setImageResource(if (switchSound.isChecked) R.drawable.ic_sound_on else R.drawable.ic_sound_off)
        switchSound.setOnCheckedChangeListener { _, isChecked ->
            Prefs.setSoundEnabled(this, isChecked)
            iconSound.setImageResource(if (isChecked) R.drawable.ic_sound_on else R.drawable.ic_sound_off)
        }
        switchHaptics.setOnCheckedChangeListener { _, isChecked -> Prefs.setHapticsEnabled(this, isChecked) }

        textTitle.popIn(0)
        textTagline.popIn(60)
        cardReaction.popIn(140)
        cardTapBattle.popIn(200)
        cardEndurance.popIn(260)
        cardPicker.popIn(320)
        btnHowToPlay.popIn(380)
        rowSound.popIn(420)
        rowHaptics.popIn(450)

        findViewById<AdView>(R.id.ad_view).loadStandardAd()
    }

    private fun openDuelSetup(mode: DuelMode) {
        val intent = Intent(this, SetupActivity::class.java).apply {
            putExtra(SetupActivity.EXTRA_DUEL_MODE, mode.name)
        }
        startActivity(intent)
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
