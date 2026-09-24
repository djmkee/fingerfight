package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent

/**
 * "Tap Battle": two players race to tap their half of the screen as fast as
 * possible. First to reach the target tap count wins the round. No holding,
 * no chasing -- just how fast you can drum your finger on your own side.
 */
class TapBattleArenaView(context: Context) : BaseArenaView(context) {

    private class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private val lastTouch = arrayOfNulls<Touch>(2)
    private val tapCounts = intArrayOf(0, 0)
    private val readySides = mutableSetOf<Int>()

    private var phase = GamePhase.LOBBY
    private var countdownText: String? = null
    private var countdownRunnable: Runnable? = null
    private var timeoutRunnable: Runnable? = null

    private val radiusPx get() = dp(40f)
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val barBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0x33FFFFFF }

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        cleanup()
        lastTouch[0] = null
        lastTouch[1] = null
        tapCounts[0] = 0
        tapCounts[1] = 0
        readySides.clear()
        countdownText = null
        phase = GamePhase.LOBBY
        listener?.onPhaseChanged(phase, context.getString(R.string.lobby_tap_battle))
        invalidate()
    }

    override fun cleanup() {
        countdownRunnable?.let { handler.removeCallbacks(it) }
        countdownRunnable = null
        timeoutRunnable?.let { handler.removeCallbacks(it) }
        timeoutRunnable = null
    }

    private fun slotForX(x: Float): Int = if (x < width / 2f) 0 else 1

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                handlePointerDown(event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    val slot = slotForX(event.getX(i))
                    lastTouch[slot]?.apply {
                        x = event.getX(i)
                        y = event.getY(i)
                    }
                }
            }
            else -> Unit
        }
        invalidate()
        return true
    }

    private fun handlePointerDown(x: Float, y: Float) {
        val slot = slotForX(x)
        lastTouch[slot] = Touch(x, y)
        spawnRipple(x, y, ColorPalette.colorFor(slot))

        when (phase) {
            GamePhase.LOBBY -> {
                readySides.add(slot)
                if (readySides.size == 2) beginCountdown()
            }
            GamePhase.ACTIVE -> {
                tapCounts[slot]++
                if (tapCounts[slot] >= TARGET_TAPS) {
                    spawnConfettiBurst(x, y)
                    endRound(slot, "Player ${slot + 1} wins the tap battle ${tapCounts[slot]}-${tapCounts[1 - slot]}!")
                }
            }
            else -> Unit
        }
    }

    private fun beginCountdown() {
        phase = GamePhase.COUNTDOWN
        var count = 3
        fun step() {
            if (count > 0) {
                countdownText = count.toString()
                listener?.onPhaseChanged(phase, context.getString(R.string.countdown_message))
                count--
                val runnable = Runnable { step() }
                countdownRunnable = runnable
                handler.postDelayed(runnable, 700)
            } else {
                countdownText = null
                phase = GamePhase.ACTIVE
                soundManager?.playReveal()
                listener?.onPhaseChanged(phase, context.getString(R.string.go_message))
                val timeout = Runnable { onTimeExpired() }
                timeoutRunnable = timeout
                handler.postDelayed(timeout, ROUND_DURATION_MS)
            }
            invalidate()
        }
        step()
    }

    private fun onTimeExpired() {
        if (phase != GamePhase.ACTIVE) return
        when {
            tapCounts[0] > tapCounts[1] -> endRound(0, "Time's up! Player 1 wins ${tapCounts[0]}-${tapCounts[1]}!")
            tapCounts[1] > tapCounts[0] -> endRound(1, "Time's up! Player 2 wins ${tapCounts[1]}-${tapCounts[0]}!")
            else -> {
                phase = GamePhase.ROUND_END
                cleanup()
                soundManager?.playRoundWin()
                val summary = "Time's up! It's a ${tapCounts[0]}-${tapCounts[1]} tie -- replay!"
                listener?.onPhaseChanged(phase, summary)
                listener?.onRoundEnded(RoundResult(emptyMap(), summary))
            }
        }
    }

    private fun endRound(winnerSlot: Int, summary: String) {
        if (phase == GamePhase.ROUND_END) return
        phase = GamePhase.ROUND_END
        cleanup()
        soundManager?.playRoundWin()
        listener?.onPhaseChanged(phase, summary)
        listener?.onRoundEnded(RoundResult(mapOf(winnerSlot to 1), summary))
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        drawRipples(canvas)

        val defaultX = arrayOf(width * 0.25f, width * 0.75f)
        for (slot in 0..1) {
            val touch = lastTouch[slot]
            val cx = touch?.x ?: defaultX[slot]
            val cy = touch?.y ?: height / 2f
            val color = ColorPalette.colorFor(slot)
            drawPlayerCircle(canvas, cx, cy, radiusPx, color, tapCounts[slot].toString())

            // Progress bar toward the target tap count.
            val barLeft = if (slot == 0) dp(24f) else width - dp(24f) - dp(90f)
            val barTop = height - dp(48f)
            val barWidth = dp(90f)
            val barHeight = dp(14f)
            canvas.drawRect(barLeft, barTop, barLeft + barWidth, barTop + barHeight, barBgPaint)
            val fillWidth = barWidth * (tapCounts[slot].toFloat() / TARGET_TAPS).coerceIn(0f, 1f)
            barPaint.color = color
            canvas.drawRect(barLeft, barTop, barLeft + fillWidth, barTop + barHeight, barPaint)
        }

        countdownText?.let {
            messagePaint.textSize = dp(56f)
            canvas.drawText(it, width / 2f, height / 2f, messagePaint)
        }
        drawConfetti(canvas)
    }

    companion object {
        private const val TARGET_TAPS = 15
        private const val ROUND_DURATION_MS = 20_000L
    }
}
