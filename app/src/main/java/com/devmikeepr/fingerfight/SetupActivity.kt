package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.card.MaterialCardView

/** Match-length (and, for Endurance/Reaction Rumble, player-count) setup for a duel championship. */
class SetupActivity : AppCompatActivity() {

    private lateinit var mode: DuelMode
    private var playerCount = 4
    private var totalRounds = 3
    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        mode = DuelMode.valueOf(intent.getStringExtra(EXTRA_DUEL_MODE) ?: DuelMode.REACTION.name)
        val usesPlayerCount = mode == DuelMode.ENDURANCE || mode == DuelMode.REACTION_RUMBLE
        playerCount = if (usesPlayerCount) 4 else mode.minPlayers

        glowBackground = findViewById(R.id.glow_background)
        val textTitle = findViewById<TextView>(R.id.text_setup_title)
        val textSubtitle = findViewById<TextView>(R.id.text_setup_subtitle)
        val cardPlayers = findViewById<MaterialCardView>(R.id.card_players)
        val textPlayerCount = findViewById<TextView>(R.id.text_player_count)
        val textPlayerRange = findViewById<TextView>(R.id.text_player_range)
        val btnMinus = findViewById<MaterialButton>(R.id.btn_players_minus)
        val btnPlus = findViewById<MaterialButton>(R.id.btn_players_plus)
        val toggleRounds = findViewById<MaterialButtonToggleGroup>(R.id.toggle_rounds)
        val btnStart = findViewById<MaterialButton>(R.id.btn_start_match)

        when (mode) {
            DuelMode.REACTION -> {
                textTitle.text = getString(R.string.setup_reaction_title)
                textSubtitle.text = getString(R.string.setup_reaction_subtitle)
            }
            DuelMode.TAP_BATTLE -> {
                textTitle.text = getString(R.string.setup_tapbattle_title)
                textSubtitle.text = getString(R.string.setup_tapbattle_subtitle)
            }
            DuelMode.ENDURANCE -> {
                textTitle.text = getString(R.string.setup_endurance_title)
                textSubtitle.text = getString(R.string.setup_endurance_subtitle)
            }
            DuelMode.REACTION_RUMBLE -> {
                textTitle.text = getString(R.string.setup_rumble_title)
                textSubtitle.text = getString(R.string.setup_rumble_subtitle)
            }
        }

        cardPlayers.visibility = if (usesPlayerCount) View.VISIBLE else View.GONE
        textPlayerCount.text = playerCount.toString()
        textPlayerRange.text = getString(R.string.player_range_format, mode.minPlayers, mode.maxPlayers)

        btnMinus.applyPressAnimation()
        btnPlus.applyPressAnimation()
        btnStart.applyPressAnimation()

        btnMinus.setOnClickListener {
            if (playerCount > mode.minPlayers) {
                playerCount--
                textPlayerCount.text = playerCount.toString()
            }
        }
        btnPlus.setOnClickListener {
            if (playerCount < mode.maxPlayers) {
                playerCount++
                textPlayerCount.text = playerCount.toString()
            }
        }

        toggleRounds.check(R.id.btn_rounds_3)
        toggleRounds.addOnButtonCheckedListener { group, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            group.hapticTick()
            totalRounds = when (checkedId) {
                R.id.btn_rounds_1 -> 1
                R.id.btn_rounds_3 -> 3
                R.id.btn_rounds_5 -> 5
                R.id.btn_rounds_7 -> 7
                else -> 3
            }
        }

        btnStart.setOnClickListener {
            val intent = Intent(this, GameActivity::class.java).apply {
                putExtra(GameActivity.EXTRA_DUEL_MODE, mode.name)
                putExtra(GameActivity.EXTRA_PLAYER_COUNT, if (usesPlayerCount) playerCount else mode.minPlayers)
                putExtra(GameActivity.EXTRA_TOTAL_ROUNDS, totalRounds)
            }
            startActivity(intent)
        }

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

    companion object {
        const val EXTRA_DUEL_MODE = "extra_duel_mode"
    }
}
