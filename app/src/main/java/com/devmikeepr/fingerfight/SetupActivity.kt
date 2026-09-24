package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup

/** Match-length setup for a Reaction Duel championship. */
class SetupActivity : AppCompatActivity() {

    private var totalRounds = 3
    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        glowBackground = findViewById(R.id.glow_background)
        val toggleRounds = findViewById<MaterialButtonToggleGroup>(R.id.toggle_rounds)
        val btnStart = findViewById<MaterialButton>(R.id.btn_start_match)

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
                putExtra(GameActivity.EXTRA_TOTAL_ROUNDS, totalRounds)
            }
            startActivity(intent)
        }
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
