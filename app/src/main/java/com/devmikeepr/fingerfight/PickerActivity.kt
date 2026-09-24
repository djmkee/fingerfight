package com.devmikeepr.fingerfight

import android.os.Bundle
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.google.android.material.button.MaterialButton

class PickerActivity : AppCompatActivity(), PickerListener {

    private lateinit var pickType: PickType
    private var purpose: PickerPurpose = PickerPurpose.WHO_PAYS
    private var customText: String = ""
    private var orderTitle: String = ""

    private lateinit var soundManager: SoundManager
    private lateinit var arenaContainer: FrameLayout
    private lateinit var glowBackground: GlowBackgroundView
    private var arenaView: PickerArenaView? = null

    private lateinit var titleText: TextView
    private lateinit var instructionText: TextView
    private lateinit var revealText: TextView
    private lateinit var orderListContainer: LinearLayout
    private lateinit var btnSpinAgain: MaterialButton
    private lateinit var btnNewSetup: MaterialButton

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        hideSystemBars()
        setContentView(R.layout.activity_picker)

        pickType = PickType.valueOf(intent.getStringExtra(EXTRA_PICK_TYPE) ?: PickType.SINGLE.name)
        purpose = PickerPurpose.valueOf(intent.getStringExtra(EXTRA_PURPOSE) ?: PickerPurpose.WHO_PAYS.name)
        customText = intent.getStringExtra(EXTRA_CUSTOM_TEXT) ?: getString(R.string.picker_custom_default)
        orderTitle = intent.getStringExtra(EXTRA_ORDER_TITLE) ?: getString(R.string.order_preset_turn)

        soundManager = SoundManager(this)

        arenaContainer = findViewById(R.id.arena_container)
        glowBackground = findViewById(R.id.glow_background)
        titleText = findViewById(R.id.picker_title_text)
        instructionText = findViewById(R.id.picker_instruction_text)
        revealText = findViewById(R.id.picker_reveal_text)
        orderListContainer = findViewById(R.id.order_list_container)
        btnSpinAgain = findViewById(R.id.btn_spin_again)
        btnNewSetup = findViewById(R.id.btn_new_setup)

        titleText.text = if (pickType == PickType.SINGLE) getString(purpose.labelRes) else orderTitle
        btnSpinAgain.visibility = View.GONE
        btnSpinAgain.setOnClickListener { startNewSpin() }
        btnNewSetup.setOnClickListener { finish() }

        startNewSpin()
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun startNewSpin() {
        arenaView?.let {
            it.cleanup()
            arenaContainer.removeView(it)
        }
        revealText.text = ""
        orderListContainer.removeAllViews()
        btnSpinAgain.visibility = View.GONE
        instructionText.text = getString(R.string.picker_instruction)

        val newArena = PickerArenaView(this, eliminationMode = pickType == PickType.ORDER)
        newArena.pickerListener = this
        newArena.soundManager = soundManager
        arenaView = newArena
        arenaContainer.addView(
            newArena,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        )
        newArena.startRound()
    }

    override fun onLobbyUpdate(fingerCount: Int) {
        runOnUiThread {
            instructionText.text = if (fingerCount == 0) {
                getString(R.string.picker_instruction)
            } else {
                resources.getQuantityString(R.plurals.picker_fingers_down, fingerCount, fingerCount)
            }
        }
    }

    override fun onPickRevealed(participantNumber: Int, remaining: Int, isFinalPick: Boolean) {
        runOnUiThread {
            if (pickType == PickType.SINGLE) {
                revealText.text = buildSingleResultText(participantNumber)
            } else {
                val row = TextView(this).apply {
                    text = getString(R.string.order_row_format, orderListContainer.childCount + 1, participantNumber)
                    setTextColor(ColorPalette.colorFor(participantNumber - 1))
                    textSize = 18f
                    setPadding(0, 6, 0, 6)
                }
                orderListContainer.addView(row)
                instructionText.text = if (!isFinalPick) {
                    resources.getQuantityString(R.plurals.picker_remaining, remaining, remaining)
                } else {
                    ""
                }
            }
        }
    }

    private fun buildSingleResultText(participantNumber: Int): String {
        return if (purpose == PickerPurpose.CUSTOM) {
            getString(R.string.purpose_custom_result_format, participantNumber, customText)
        } else {
            getString(purpose.resultTemplateRes, participantNumber)
        }
    }

    override fun onAllDone() {
        runOnUiThread {
            btnSpinAgain.visibility = View.VISIBLE
            if (pickType == PickType.ORDER) {
                instructionText.text = getString(R.string.picker_order_done)
            }
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

    override fun onDestroy() {
        super.onDestroy()
        arenaView?.cleanup()
        soundManager.release()
    }

    companion object {
        const val EXTRA_PICK_TYPE = "extra_pick_type"
        const val EXTRA_PURPOSE = "extra_purpose"
        const val EXTRA_CUSTOM_TEXT = "extra_custom_text"
        const val EXTRA_ORDER_TITLE = "extra_order_title"
    }
}
