package com.devmikeepr.fingerfight

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton

/**
 * Hosts a duel championship (Reaction, Tap Battle, or Endurance): played over
 * a configurable number of rounds with a running scoreboard.
 */
class GameActivity : AppCompatActivity(), ArenaListener {

    private lateinit var mode: DuelMode
    private var playerCount = 2
    private var totalRounds = 1
    private var currentRound = 1

    private lateinit var playerSlots: List<PlayerSlot>
    private lateinit var soundManager: SoundManager
    private lateinit var arenaContainer: FrameLayout
    private lateinit var glowBackground: GlowBackgroundView
    private var arenaView: BaseArenaView? = null

    private lateinit var hudRoundText: TextView
    private lateinit var hudMessageText: TextView
    private lateinit var hudTimerText: TextView
    private lateinit var resultOverlay: View
    private lateinit var resultTitle: TextView
    private lateinit var resultSummary: TextView
    private lateinit var leaderboardContainer: LinearLayout
    private lateinit var btnPrimary: MaterialButton
    private lateinit var btnSecondary: MaterialButton

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        hideSystemBars()
        setContentView(R.layout.activity_game)

        mode = DuelMode.valueOf(intent.getStringExtra(EXTRA_DUEL_MODE) ?: DuelMode.REACTION.name)
        playerCount = intent.getIntExtra(EXTRA_PLAYER_COUNT, mode.minPlayers)
        totalRounds = intent.getIntExtra(EXTRA_TOTAL_ROUNDS, 1)
        playerSlots = List(playerCount) { PlayerSlot(it, ColorPalette.colorFor(it)) }

        soundManager = SoundManager(this)

        arenaContainer = findViewById(R.id.arena_container)
        glowBackground = findViewById(R.id.glow_background)
        hudRoundText = findViewById(R.id.hud_round_text)
        hudMessageText = findViewById(R.id.hud_message_text)
        hudTimerText = findViewById(R.id.hud_timer_text)
        resultOverlay = findViewById(R.id.result_overlay)
        resultTitle = findViewById(R.id.result_title)
        resultSummary = findViewById(R.id.result_summary)
        leaderboardContainer = findViewById(R.id.leaderboard_container)
        btnPrimary = findViewById(R.id.btn_primary)
        btnSecondary = findViewById(R.id.btn_secondary)

        btnSecondary.text = getString(R.string.action_main_menu)
        btnSecondary.visibility = View.GONE
        btnSecondary.setOnClickListener { finish() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                confirmQuit()
            }
        })

        findViewById<AdView>(R.id.ad_view).loadStandardAd()

        startNewRound()
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun confirmQuit() {
        AlertDialog.Builder(this)
            .setMessage(R.string.action_quit_confirm)
            .setPositiveButton(R.string.action_quit) { _, _ -> finish() }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun startNewRound() {
        resultOverlay.visibility = View.GONE
        arenaView?.let {
            it.cleanup()
            arenaContainer.removeView(it)
        }

        val newArena: BaseArenaView = when (mode) {
            DuelMode.REACTION -> ReactionArenaView(this)
            DuelMode.TAP_BATTLE -> TapBattleArenaView(this)
            DuelMode.ENDURANCE -> EnduranceArenaView(this, playerCount)
        }
        newArena.listener = this
        newArena.soundManager = soundManager
        arenaView = newArena
        arenaContainer.addView(
            newArena,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        )

        hudRoundText.text = getString(R.string.hud_round_format, currentRound, totalRounds)
        hudTimerText.text = ""
        newArena.startRound()
    }

    override fun onPhaseChanged(phase: GamePhase, message: String) {
        runOnUiThread {
            hudMessageText.text = message
            if (phase != GamePhase.ACTIVE) {
                hudTimerText.text = ""
            }
        }
    }

    override fun onTimerTick(secondsRemaining: Int) {
        runOnUiThread {
            hudTimerText.text = "0:%02d".format(secondsRemaining.coerceAtLeast(0))
        }
    }

    override fun onRoundEnded(result: RoundResult) {
        runOnUiThread {
            result.pointsAwarded.forEach { (slot, points) ->
                playerSlots[slot].totalScore += points
            }
            showResultOverlay(result)
        }
    }

    private fun showResultOverlay(result: RoundResult) {
        val isFinalRound = currentRound >= totalRounds
        resultTitle.text = if (isFinalRound) {
            getString(R.string.result_final_title)
        } else {
            getString(R.string.result_round_title_format, currentRound)
        }
        resultSummary.text = result.summary

        leaderboardContainer.removeAllViews()
        val sorted = playerSlots.sortedByDescending { it.totalScore }
        sorted.forEach { slot ->
            val row = TextView(this).apply {
                text = getString(R.string.leaderboard_row_format, slot.index + 1, slot.totalScore)
                setTextColor(slot.color)
                textSize = 16f
                setPadding(0, 8, 0, 8)
            }
            leaderboardContainer.addView(row)
        }

        if (isFinalRound) {
            val topScore = sorted.first().totalScore
            val champions = sorted.filter { it.totalScore == topScore }
            val championText = if (champions.size == 1) {
                getString(R.string.champion_row_format, champions.first().index + 1)
            } else {
                getString(
                    R.string.co_champion_row_format,
                    champions.joinToString(", ") { (it.index + 1).toString() }
                )
            }
            val championRow = TextView(this).apply {
                text = championText
                setTextColor(Color.WHITE)
                textSize = 16f
                setPadding(0, 16, 0, 8)
            }
            leaderboardContainer.addView(championRow, 0)

            btnPrimary.text = getString(R.string.action_play_again)
            btnPrimary.setOnClickListener {
                currentRound = 1
                playerSlots.forEach { it.totalScore = 0 }
                startNewRound()
            }
            btnSecondary.visibility = View.VISIBLE
        } else {
            btnPrimary.text = getString(R.string.action_next_round)
            btnPrimary.setOnClickListener {
                currentRound++
                startNewRound()
            }
            btnSecondary.visibility = View.GONE
        }

        resultOverlay.visibility = View.VISIBLE
    }

    override fun onResume() {
        super.onResume()
        glowBackground.startAnimating()
    }

    override fun onPause() {
        super.onPause()
        glowBackground.stopAnimating()
    }

    override fun onDestroy() {
        super.onDestroy()
        arenaView?.cleanup()
        soundManager.release()
    }

    companion object {
        const val EXTRA_DUEL_MODE = "extra_duel_mode"
        const val EXTRA_PLAYER_COUNT = "extra_player_count"
        const val EXTRA_TOTAL_ROUNDS = "extra_total_rounds"
    }
}
