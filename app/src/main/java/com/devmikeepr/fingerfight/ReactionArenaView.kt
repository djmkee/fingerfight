package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import kotlin.random.Random

/**
 * "Reaction Duel": two players each hold a finger on their half of the screen. After a
 * hidden random delay the screen flashes "GO!" and whoever lifts their finger first wins
 * the point. Lifting before "GO!" is an automatic false-start loss.
 */
class ReactionArenaView(context: Context) : BaseArenaView(context) {

    private data class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private val slotTouches = mutableMapOf<Int, Touch>()
    private val pointerToSlot = mutableMapOf<Int, Int>()

    private var phase = GamePhase.LOBBY
    private var goRunnable: Runnable? = null
    private var timeoutRunnable: Runnable? = null
    private var goElapsedRealtime = 0L
    private var flashGo = false

    private val radiusPx get() = dp(48f)

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        slotTouches.clear()
        pointerToSlot.clear()
        phase = GamePhase.LOBBY
        flashGo = false
        cleanup()
        listener?.onPhaseChanged(phase, context.getString(R.string.lobby_reaction))
        invalidate()
    }

    override fun cleanup() {
        goRunnable?.let { handler.removeCallbacks(it) }
        goRunnable = null
        timeoutRunnable?.let { handler.removeCallbacks(it) }
        timeoutRunnable = null
    }

    private fun slotForX(x: Float): Int = if (x < width / 2f) 0 else 1

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                handlePointerDown(event.getPointerId(index), event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    val slot = pointerToSlot[event.getPointerId(i)] ?: continue
                    slotTouches[slot]?.apply {
                        x = event.getX(i)
                        y = event.getY(i)
                    }
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> {
                val index = event.actionIndex
                handlePointerUp(event.getPointerId(index))
            }
            MotionEvent.ACTION_CANCEL -> {
                pointerToSlot.keys.toList().forEach { handlePointerUp(it) }
            }
        }
        invalidate()
        return true
    }

    private fun handlePointerDown(pointerId: Int, x: Float, y: Float) {
        if (phase != GamePhase.LOBBY) return
        val slot = slotForX(x)
        if (pointerToSlot.containsValue(slot)) return
        pointerToSlot[pointerId] = slot
        slotTouches[slot] = Touch(x, y)
        spawnRipple(x, y, ColorPalette.colorFor(slot))
        if (slotTouches.size == 2) {
            beginCountdown()
        }
    }

    private fun handlePointerUp(pointerId: Int) {
        val slot = pointerToSlot[pointerId] ?: return
        when (phase) {
            GamePhase.LOBBY -> {
                pointerToSlot.remove(pointerId)
                slotTouches.remove(slot)
            }
            GamePhase.COUNTDOWN -> {
                val winner = 1 - slot
                endRound(
                    mapOf(winner to 1),
                    "Player ${slot + 1} false-started! Player ${winner + 1} wins the round."
                )
            }
            GamePhase.ACTIVE -> {
                val reactionMs = SystemClock.elapsedRealtime() - goElapsedRealtime
                slotTouches[slot]?.let { spawnConfettiBurst(it.x, it.y) }
                endRound(mapOf(slot to 1), "Player ${slot + 1} reacted in ${reactionMs}ms!")
            }
            GamePhase.ROUND_END -> Unit
        }
    }

    private fun beginCountdown() {
        phase = GamePhase.COUNTDOWN
        listener?.onPhaseChanged(phase, context.getString(R.string.countdown_message))
        val delay = Random.nextLong(1500, 4000)
        val runnable = Runnable { triggerGo() }
        goRunnable = runnable
        handler.postDelayed(runnable, delay)
    }

    private fun triggerGo() {
        if (phase != GamePhase.COUNTDOWN) return
        phase = GamePhase.ACTIVE
        flashGo = true
        goElapsedRealtime = SystemClock.elapsedRealtime()
        soundManager?.playReveal()
        spawnRipple(width / 2f, height / 2f, 0xFF00E5FF.toInt())
        listener?.onPhaseChanged(phase, context.getString(R.string.go_message))
        val timeout = Runnable {
            if (phase == GamePhase.ACTIVE) {
                endRound(emptyMap(), "No one reacted in time — replay!")
            }
        }
        timeoutRunnable = timeout
        handler.postDelayed(timeout, 8000)
        invalidate()
    }

    private fun endRound(pointsAwarded: Map<Int, Int>, summary: String) {
        if (phase == GamePhase.ROUND_END) return
        phase = GamePhase.ROUND_END
        cleanup()
        soundManager?.playRoundWin()
        listener?.onPhaseChanged(phase, summary)
        listener?.onRoundEnded(RoundResult(pointsAwarded, summary))
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (flashGo) {
            canvas.drawColor(0x3300E676)
        }
        drawRipples(canvas)
        val defaultX = arrayOf(width * 0.25f, width * 0.75f)
        for (slot in 0..1) {
            val touch = slotTouches[slot]
            val cx = touch?.x ?: defaultX[slot]
            val cy = touch?.y ?: height / 2f
            val color = if (touch != null) ColorPalette.colorFor(slot) else Color.DKGRAY
            drawPlayerCircle(canvas, cx, cy, radiusPx, color, (slot + 1).toString())
        }
        if (flashGo) {
            messagePaint.textSize = dp(40f)
            canvas.drawText(context.getString(R.string.go_message), width / 2f, height / 2f, messagePaint)
        }
        drawConfetti(canvas)
    }
}
