package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup

class SetupActivity : AppCompatActivity() {

    private var mode = GameMode.CHASE
    private var playerCount = 4
    private var totalRounds = 3

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        val toggleMode = findViewById<MaterialButtonToggleGroup>(R.id.toggle_mode)
        val toggleRounds = findViewById<MaterialButtonToggleGroup>(R.id.toggle_rounds)
        val rowPlayers = findViewById<View>(R.id.row_players)
        val labelPlayers = findViewById<View>(R.id.label_players)
        val textPlayerCount = findViewById<TextView>(R.id.text_player_count)
        val btnMinus = findViewById<MaterialButton>(R.id.btn_players_minus)
        val btnPlus = findViewById<MaterialButton>(R.id.btn_players_plus)
        val btnStart = findViewById<MaterialButton>(R.id.btn_start_match)

        fun updatePlayerVisibility() {
            val visible = mode == GameMode.CHASE
            rowPlayers.visibility = if (visible) View.VISIBLE else View.GONE
            labelPlayers.visibility = if (visible) View.VISIBLE else View.GONE
        }

        fun updatePlayerCountText() {
            textPlayerCount.text = playerCount.toString()
        }

        toggleMode.check(R.id.btn_mode_chase)
        updatePlayerVisibility()
        updatePlayerCountText()

        toggleMode.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            mode = if (checkedId == R.id.btn_mode_chase) GameMode.CHASE else GameMode.REACTION
            updatePlayerVisibility()
        }

        btnMinus.setOnClickListener {
            if (playerCount > GameMode.CHASE.minPlayers) {
                playerCount--
                updatePlayerCountText()
            }
        }
        btnPlus.setOnClickListener {
            if (playerCount < GameMode.CHASE.maxPlayers) {
                playerCount++
                updatePlayerCountText()
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
            val finalPlayerCount = if (mode == GameMode.REACTION) GameMode.REACTION.minPlayers else playerCount
            val intent = Intent(this, GameActivity::class.java).apply {
                putExtra(GameActivity.EXTRA_MODE, mode.name)
                putExtra(GameActivity.EXTRA_PLAYER_COUNT, finalPlayerCount)
                putExtra(GameActivity.EXTRA_TOTAL_ROUNDS, totalRounds)
            }
            startActivity(intent)
        }
    }
}
