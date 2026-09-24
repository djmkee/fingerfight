package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import kotlin.random.Random

/**
 * "Reaction Rumble": the multiplayer sibling of Reaction Duel. Everyone
 * holds a finger down at once; after a hidden random delay the screen
 * flashes "GO!" and the first to lift wins. Lifting before "GO!" only
 * disqualifies that one player -- the round keeps going for everyone else.
 */
class ReactionRumbleArenaView(context: Context, private val playerCount: Int) : BaseArenaView(context) {

    private class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private val slotTouches = mutableMapOf<Int, Touch>()
    private val pointerToSlot = mutableMapOf<Int, Int>()
    private val disqualified = mutableSetOf<Int>()

    private var phase = GamePhase.LOBBY
    private var flashGo = false
    private var countdownRunnable: Runnable? = null
    private var timeoutRunnable: Runnable? = null

    private val radiusPx get() = dp(40f)

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        cleanup()
        slotTouches.clear()
        pointerToSlot.clear()
        disqualified.clear()
        flashGo = false
        phase = GamePhase.LOBBY
        listener?.onPhaseChanged(
            phase,
            context.getString(R.string.lobby_endurance_format, playerCount, 0, playerCount)
        )
        invalidate()
    }

    override fun cleanup() {
        countdownRunnable?.let { handler.removeCallbacks(it) }
        countdownRunnable = null
        timeoutRunnable?.let { handler.removeCallbacks(it) }
        timeoutRunnable = null
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                handlePointerDown(event.getPointerId(index), event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    val slot = pointerToSlot[event.getPointerId(i)] ?: continue
                    if (slot in disqualified) continue
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
        if (pointerToSlot.size >= playerCount) return
        val slot = pointerToSlot.size
        pointerToSlot[pointerId] = slot
        slotTouches[slot] = Touch(x, y)
        spawnRipple(x, y, ColorPalette.colorFor(slot))
        listener?.onPhaseChanged(
            phase,
            context.getString(R.string.lobby_endurance_format, playerCount, pointerToSlot.size, playerCount)
        )
        if (pointerToSlot.size == playerCount) {
            beginCountdown()
        }
    }

    private fun handlePointerUp(pointerId: Int) {
        val slot = pointerToSlot[pointerId] ?: return
        when (phase) {
            GamePhase.LOBBY -> {
                pointerToSlot.remove(pointerId)
                slotTouches.remove(slot)
                reindexLobbySlots()
                listener?.onPhaseChanged(
                    phase,
                    context.getString(R.string.lobby_endurance_format, playerCount, pointerToSlot.size, playerCount)
                )
            }
            GamePhase.COUNTDOWN -> disqualifyEarly(slot)
            GamePhase.ACTIVE -> {
                if (slot !in disqualified) {
                    spawnConfettiBurst(slotTouches[slot]?.x ?: width / 2f, slotTouches[slot]?.y ?: height / 2f)
                    endRound(mapOf(slot to 1), "Player ${slot + 1} reacted first!")
                }
            }
            else -> Unit
        }
    }

    private fun reindexLobbySlots() {
        val remainingPointerIds = pointerToSlot.keys.toList()
        val remainingTouches = remainingPointerIds.map { slotTouches[pointerToSlot[it]] }
        pointerToSlot.clear()
        slotTouches.clear()
        remainingPointerIds.forEachIndexed { newSlot, pointerId ->
            pointerToSlot[pointerId] = newSlot
            remainingTouches[newSlot]?.let { slotTouches[newSlot] = it }
        }
    }

    private fun disqualifyEarly(slot: Int) {
        if (slot in disqualified) return
        disqualified.add(slot)
        soundManager?.playTag()
        listener?.onPhaseChanged(phase, context.getString(R.string.early_lift_format, slot + 1))
        if (disqualified.size == playerCount) {
            endRound(emptyMap(), "Everyone jumped the gun -- replay!")
            return
        }
        if (disqualified.size == playerCount - 1) {
            // Only one eligible player remains: no point making them wait for GO.
            val winner = (0 until playerCount).first { it !in disqualified }
            countdownRunnable?.let { handler.removeCallbacks(it) }
            endRound(mapOf(winner to 1), "Player ${winner + 1} is the last one standing!")
        }
    }

    private fun beginCountdown() {
        phase = GamePhase.COUNTDOWN
        listener?.onPhaseChanged(phase, context.getString(R.string.countdown_message))
        val delay = Random.nextLong(1500, 4000)
        val runnable = Runnable { triggerGo() }
        countdownRunnable = runnable
        handler.postDelayed(runnable, delay)
    }

    private fun triggerGo() {
        if (phase != GamePhase.COUNTDOWN) return
        phase = GamePhase.ACTIVE
        flashGo = true
        soundManager?.playReveal()
        spawnRipple(width / 2f, height / 2f, 0xFF00E5FF.toInt())
        listener?.onPhaseChanged(phase, context.getString(R.string.go_message))
        val timeout = Runnable {
            if (phase == GamePhase.ACTIVE) {
                endRound(emptyMap(), "Nobody reacted in time -- replay!")
            }
        }
        timeoutRunnable = timeout
        handler.postDelayed(timeout, 10_000)
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
        for (slot in 0 until playerCount) {
            val touch = slotTouches[slot] ?: continue
            val isOut = slot in disqualified
            val color = if (isOut) ColorPalette.DIM_COLOR else ColorPalette.colorFor(slot)
            drawPlayerCircle(canvas, touch.x, touch.y, radiusPx, color, (slot + 1).toString(), alpha = if (isOut) 0.5f else 1f)
        }
        if (flashGo) {
            messagePaint.textSize = dp(40f)
            canvas.drawText(context.getString(R.string.go_message), width / 2f, height / 2f, messagePaint)
        }
        drawConfetti(canvas)
    }
}
