package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup

/** Match-length (and, for Endurance, player-count) setup for a duel championship. */
class SetupActivity : AppCompatActivity() {

    private lateinit var mode: DuelMode
    private var playerCount = 4
    private var totalRounds = 3
    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        mode = DuelMode.valueOf(intent.getStringExtra(EXTRA_DUEL_MODE) ?: DuelMode.REACTION.name)
        playerCount = mode.minPlayers.coerceAtLeast(if (mode == DuelMode.ENDURANCE) 4 else mode.minPlayers)

        glowBackground = findViewById(R.id.glow_background)
        val textTitle = findViewById<TextView>(R.id.text_setup_title)
        val textSubtitle = findViewById<TextView>(R.id.text_setup_subtitle)
        val labelPlayers = findViewById<View>(R.id.label_players)
        val rowPlayers = findViewById<View>(R.id.row_players)
        val textPlayerCount = findViewById<TextView>(R.id.text_player_count)
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
        }

        val showPlayerCount = mode == DuelMode.ENDURANCE
        labelPlayers.visibility = if (showPlayerCount) View.VISIBLE else View.GONE
        rowPlayers.visibility = if (showPlayerCount) View.VISIBLE else View.GONE
        textPlayerCount.text = playerCount.toString()

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
        toggleRounds.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
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
                putExtra(GameActivity.EXTRA_PLAYER_COUNT, if (mode == DuelMode.ENDURANCE) playerCount else mode.minPlayers)
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
