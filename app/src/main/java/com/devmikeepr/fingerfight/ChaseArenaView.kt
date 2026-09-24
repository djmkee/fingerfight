package com.devmikeepr.fingerfight

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.os.CountDownTimer
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import kotlin.math.hypot
import kotlin.random.Random

/**
 * "Chase Duel": every player holds a finger on screen. After a hidden random delay one
 * finger is secretly picked as the Hunter and must tag the others before the round timer
 * runs out. Lifting your finger early (hunter included) is an instant loss for that player.
 */
class ChaseArenaView(context: Context, private val playerCount: Int) : BaseArenaView(context) {

    private data class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private val slotTouches = mutableMapOf<Int, Touch>()
    private val pointerToSlot = mutableMapOf<Int, Int>()
    private val eliminatedSlots = mutableSetOf<Int>()
    private val eliminationProgress = mutableMapOf<Int, Float>()
    private val eliminationAnimators = mutableMapOf<Int, ValueAnimator>()

    private var phase = GamePhase.LOBBY
    private var hunterSlot = -1
    private var hunterPulse = 1f
    private var hunterPulseAnimator: ValueAnimator? = null

    private var revealRunnable: Runnable? = null
    private var bannerHideRunnable: Runnable? = null
    private var roundTimer: CountDownTimer? = null
    private var bannerText: String? = null

    private val radiusPx get() = dp(42f)

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        slotTouches.clear()
        pointerToSlot.clear()
        eliminatedSlots.clear()
        eliminationAnimators.values.forEach { it.cancel() }
        eliminationAnimators.clear()
        eliminationProgress.clear()
        hunterSlot = -1
        hunterPulseAnimator?.cancel()
        hunterPulseAnimator = null
        hunterPulse = 1f
        bannerText = null
        cleanup()
        phase = GamePhase.LOBBY
        listener?.onPhaseChanged(
            phase,
            context.getString(R.string.lobby_chase_format, playerCount, 0, playerCount)
        )
        invalidate()
    }

    override fun cleanup() {
        revealRunnable?.let { handler.removeCallbacks(it) }
        revealRunnable = null
        bannerHideRunnable?.let { handler.removeCallbacks(it) }
        bannerHideRunnable = null
        roundTimer?.cancel()
        roundTimer = null
        hunterPulseAnimator?.cancel()
        eliminationAnimators.values.forEach { it.cancel() }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                handlePointerDown(event.getPointerId(index), event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    handlePointerMove(event.getPointerId(i), event.getX(i), event.getY(i))
                }
                if (phase == GamePhase.ACTIVE) checkCollisions()
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
        listener?.onPhaseChanged(
            phase,
            context.getString(R.string.lobby_chase_format, playerCount, pointerToSlot.size, playerCount)
        )
        if (pointerToSlot.size == playerCount) {
            beginCountdown()
        }
    }

    private fun handlePointerMove(pointerId: Int, x: Float, y: Float) {
        val slot = pointerToSlot[pointerId] ?: return
        if (slot in eliminatedSlots) return
        slotTouches[slot]?.apply {
            this.x = x
            this.y = y
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
                    context.getString(R.string.lobby_chase_format, playerCount, pointerToSlot.size, playerCount)
                )
            }
            GamePhase.COUNTDOWN, GamePhase.ACTIVE -> {
                if (slot !in eliminatedSlots) {
                    eliminate(slot, earlyLift = true)
                }
            }
            GamePhase.ROUND_END -> Unit
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

    private fun beginCountdown() {
        phase = GamePhase.COUNTDOWN
        listener?.onPhaseChanged(phase, context.getString(R.string.countdown_message))
        val delay = Random.nextLong(1500, 4000)
        val runnable = Runnable { revealHunter() }
        revealRunnable = runnable
        handler.postDelayed(runnable, delay)
    }

    private fun revealHunter() {
        if (phase != GamePhase.COUNTDOWN) return
        val candidates = slotTouches.keys.filter { it !in eliminatedSlots }
        if (candidates.isEmpty()) return
        hunterSlot = candidates.random()
        phase = GamePhase.ACTIVE
        soundManager?.playReveal()
        bannerText = context.getString(R.string.hunter_revealed_format, hunterSlot + 1)
        showBannerTemporarily()
        listener?.onPhaseChanged(phase, bannerText ?: "")
        if (allAliveSlotsExcludingHunter().isEmpty()) {
            endRound(mapOf(hunterSlot to 2), context.getString(R.string.hunter_revealed_format, hunterSlot + 1))
            return
        }
        startHunterPulse()
        startRoundTimer()
    }

    private fun showBannerTemporarily() {
        bannerHideRunnable?.let { handler.removeCallbacks(it) }
        val runnable = Runnable {
            bannerText = null
            invalidate()
        }
        bannerHideRunnable = runnable
        handler.postDelayed(runnable, 1400)
    }

    private fun startHunterPulse() {
        hunterPulseAnimator?.cancel()
        hunterPulseAnimator = ValueAnimator.ofFloat(1f, 1.25f).apply {
            duration = 450
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener {
                hunterPulse = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    private fun startRoundTimer() {
        roundTimer = object : CountDownTimer(ROUND_DURATION_MS, 100) {
            override fun onTick(millisUntilFinished: Long) {
                listener?.onTimerTick((millisUntilFinished / 1000).toInt() + 1)
            }

            override fun onFinish() {
                onTimeExpired()
            }
        }.start()
    }

    private fun onTimeExpired() {
        if (phase != GamePhase.ACTIVE) return
        val survivors = allAliveSlotsExcludingHunter()
        endRound(
            survivors.associateWith { 1 },
            "Time's up! ${survivors.size} survivor(s) win the round."
        )
    }

    private fun allAliveSlotsExcludingHunter(): List<Int> =
        (0 until playerCount).filter { it != hunterSlot && it !in eliminatedSlots }

    private fun checkCollisions() {
        if (hunterSlot == -1 || hunterSlot in eliminatedSlots) return
        val hunterTouch = slotTouches[hunterSlot] ?: return
        val threshold = radiusPx * 2f
        for (slot in allAliveSlotsExcludingHunter()) {
            val touch = slotTouches[slot] ?: continue
            val dist = hypot((touch.x - hunterTouch.x).toDouble(), (touch.y - hunterTouch.y).toDouble())
            if (dist <= threshold) {
                eliminate(slot, earlyLift = false)
            }
        }
    }

    private fun eliminate(slot: Int, earlyLift: Boolean) {
        if (phase != GamePhase.COUNTDOWN && phase != GamePhase.ACTIVE) return
        if (slot == hunterSlot) {
            val survivors = allAliveSlotsExcludingHunter()
            endRound(
                survivors.associateWith { 1 },
                "The Hunter forfeited! ${survivors.size} survivor(s) win the round."
            )
            return
        }
        if (slot in eliminatedSlots) return
        eliminatedSlots.add(slot)
        soundManager?.playTag()
        animateElimination(slot)
        if (earlyLift) {
            listener?.onPhaseChanged(phase, context.getString(R.string.early_lift_format, slot + 1))
        }
        if (hunterSlot != -1 && allAliveSlotsExcludingHunter().isEmpty()) {
            endRound(
                mapOf(hunterSlot to 2),
                "Player ${hunterSlot + 1} the Hunter caught everyone!"
            )
        }
    }

    private fun animateElimination(slot: Int) {
        eliminationAnimators[slot]?.cancel()
        eliminationProgress[slot] = 1f
        val animator = ValueAnimator.ofFloat(1f, 0f).apply {
            duration = 300
            addUpdateListener {
                eliminationProgress[slot] = it.animatedValue as Float
                invalidate()
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    eliminationProgress.remove(slot)
                    slotTouches.remove(slot)
                    eliminationAnimators.remove(slot)
                }
            })
            start()
        }
        eliminationAnimators[slot] = animator
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
        for (slot in 0 until playerCount) {
            val progress = eliminationProgress[slot]
            if (slot in eliminatedSlots && progress == null) continue
            val touch = slotTouches[slot] ?: continue
            val alpha = progress ?: 1f
            val isHunter = slot == hunterSlot
            val radius = radiusPx * (if (isHunter) hunterPulse else 1f) * alpha.coerceAtLeast(0.01f)
            drawPlayerCircle(
                canvas,
                touch.x,
                touch.y,
                radius,
                if (isHunter) ColorPalette.HUNTER_COLOR else ColorPalette.colorFor(slot),
                (slot + 1).toString(),
                alpha = alpha
            )
            if (isHunter) {
                ringPaint.color = ColorPalette.HUNTER_COLOR
                ringPaint.alpha = (255 * alpha).toInt().coerceIn(0, 255)
                canvas.drawCircle(touch.x, touch.y, radius + dp(6f), ringPaint)
            }
        }

        bannerText?.let {
            messagePaint.textSize = dp(22f)
            canvas.drawText(it, width / 2f, height * 0.15f, messagePaint)
        }
    }

    companion object {
        private const val ROUND_DURATION_MS = 20_000L
    }
}
