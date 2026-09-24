package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent

/**
 * "Endurance": every player holds a finger down at once. Whoever lifts
 * first is out; the last finger still touching the screen wins. No hidden
 * hunter, no chasing -- just a battle of patience.
 */
class EnduranceArenaView(context: Context, private val playerCount: Int) : BaseArenaView(context) {

    private class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private val slotTouches = mutableMapOf<Int, Touch>()
    private val pointerToSlot = mutableMapOf<Int, Int>()
    private val eliminatedSlots = mutableSetOf<Int>()
    private val finalElapsedMs = mutableMapOf<Int, Long>()

    private var phase = GamePhase.LOBBY
    private var activeStartTime = 0L
    private var tickRunnable: Runnable? = null

    private val radiusPx get() = dp(42f)

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        cleanup()
        slotTouches.clear()
        pointerToSlot.clear()
        eliminatedSlots.clear()
        finalElapsedMs.clear()
        cancelEliminationFades()
        phase = GamePhase.LOBBY
        listener?.onPhaseChanged(
            phase,
            context.getString(R.string.lobby_endurance_format, playerCount, 0, playerCount)
        )
        invalidate()
    }

    override fun cleanup() {
        tickRunnable?.let { handler.removeCallbacks(it) }
        tickRunnable = null
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
                    if (slot in eliminatedSlots) continue
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
            beginHold()
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
            GamePhase.ACTIVE -> eliminate(slot)
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

    private fun beginHold() {
        phase = GamePhase.ACTIVE
        activeStartTime = System.currentTimeMillis()
        soundManager?.playReveal()
        listener?.onPhaseChanged(phase, context.getString(R.string.endurance_hold_message))
        scheduleTick()
    }

    private fun scheduleTick() {
        val runnable = object : Runnable {
            override fun run() {
                if (phase != GamePhase.ACTIVE) return
                invalidate()
                if (System.currentTimeMillis() - activeStartTime >= MAX_HOLD_DURATION_MS) {
                    onTimeExpired()
                    return
                }
                handler.postDelayed(this, 150)
            }
        }
        tickRunnable = runnable
        handler.postDelayed(runnable, 150)
    }

    private fun aliveSlots(): List<Int> = (0 until playerCount).filter { it !in eliminatedSlots }

    private fun eliminate(slot: Int) {
        if (phase != GamePhase.ACTIVE || slot in eliminatedSlots) return
        eliminatedSlots.add(slot)
        finalElapsedMs[slot] = System.currentTimeMillis() - activeStartTime
        soundManager?.playTag()
        spawnEliminationFade(slot) { slotTouches.remove(slot) }

        val alive = aliveSlots()
        when {
            alive.size == 1 -> {
                val winner = alive.first()
                val seconds = "%.1f".format((System.currentTimeMillis() - activeStartTime) / 1000f)
                slotTouches[winner]?.let { spawnConfettiBurst(it.x, it.y) }
                endRound(mapOf(winner to 1), "Player ${winner + 1} held on the longest! (${seconds}s)")
            }
            alive.isEmpty() -> {
                val maxElapsed = finalElapsedMs.values.maxOrNull() ?: 0L
                val coWinners = finalElapsedMs.filterValues { it == maxElapsed }.keys.toList()
                endRound(coWinners.associateWith { 1 }, "Everyone let go at once -- it's a tie!")
            }
        }
    }

    private fun onTimeExpired() {
        if (phase != GamePhase.ACTIVE) return
        val survivors = aliveSlots()
        endRound(
            survivors.associateWith { 1 },
            "Time's up! ${survivors.size} player(s) held on and share the win."
        )
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
        drawRipples(canvas)
        val now = System.currentTimeMillis()
        for (slot in 0 until playerCount) {
            val touch = slotTouches[slot] ?: continue
            val alpha = eliminationAlpha(slot)
            val radius = radiusPx * alpha.coerceAtLeast(0.01f)
            drawPlayerCircle(canvas, touch.x, touch.y, radius, ColorPalette.colorFor(slot), (slot + 1).toString(), alpha)

            if (phase == GamePhase.ACTIVE || isFadingOut(slot)) {
                val elapsedMs = finalElapsedMs[slot] ?: (now - activeStartTime).coerceAtLeast(0L)
                val seconds = "%.1fs".format(elapsedMs / 1000f)
                drawSubLabel(canvas, touch.x, touch.y + radiusPx + dp(20f), seconds, dp(14f), alpha)
            }
        }
        drawConfetti(canvas)
    }

    companion object {
        private const val MAX_HOLD_DURATION_MS = 45_000L
    }
}
