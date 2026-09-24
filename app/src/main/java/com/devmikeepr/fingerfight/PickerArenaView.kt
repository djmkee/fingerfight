package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import kotlin.random.Random

private enum class PickerPhase { LOBBY, SPINNING, REVEALED, DONE }

/**
 * Shared engine for the three "party decision" modes:
 *  - single pick: everyone places a finger, one is randomly chosen, done.
 *  - full order: repeatedly picks one finger at a time until every finger
 *    held down at lock-in has been given a rank.
 *  - team split: everyone placed a finger gets randomly, evenly divided
 *    into two teams.
 *
 * Unlike the duel arenas, headcount isn't configured ahead of time — it's
 * simply "however many fingers are on the screen when the grace period
 * expires" (minimum 2). This sidesteps the physical problem with chasing a
 * finger around a phone screen: nobody has to move at all.
 */
class PickerArenaView(
    context: Context,
    private val mode: PickType
) : BaseArenaView(context) {

    private class Touch(var x: Float, var y: Float)

    private val handler = Handler(Looper.getMainLooper())

    private var nextParticipantNumber = 1
    private val pointerToParticipant = mutableMapOf<Int, Int>()
    private val activeTouches = mutableMapOf<Int, Touch>()

    private var phase = PickerPhase.LOBBY
    private var highlightedParticipant = -1
    private var winnerParticipant = -1
    private val teamAssignment = mutableMapOf<Int, Int>()
    private val flickerTeam = mutableMapOf<Int, Int>()

    private var lockInRunnable: Runnable? = null
    private var spinStepRunnable: Runnable? = null
    private var advanceRunnable: Runnable? = null

    var pickerListener: PickerListener? = null

    private val radiusPx get() = dp(40f)

    init {
        setBackgroundColor(Color.TRANSPARENT)
    }

    override fun startRound() {
        cleanup()
        pointerToParticipant.clear()
        activeTouches.clear()
        teamAssignment.clear()
        flickerTeam.clear()
        nextParticipantNumber = 1
        phase = PickerPhase.LOBBY
        highlightedParticipant = -1
        winnerParticipant = -1
        pickerListener?.onLobbyUpdate(0)
        invalidate()
    }

    override fun cleanup() {
        lockInRunnable?.let { handler.removeCallbacks(it) }
        lockInRunnable = null
        spinStepRunnable?.let { handler.removeCallbacks(it) }
        spinStepRunnable = null
        advanceRunnable?.let { handler.removeCallbacks(it) }
        advanceRunnable = null
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                handlePointerDown(event.getPointerId(index), event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    val participant = pointerToParticipant[event.getPointerId(i)] ?: continue
                    activeTouches[participant]?.let {
                        it.x = event.getX(i)
                        it.y = event.getY(i)
                    }
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> {
                val index = event.actionIndex
                handlePointerUp(event.getPointerId(index))
            }
            MotionEvent.ACTION_CANCEL -> {
                pointerToParticipant.keys.toList().forEach { handlePointerUp(it) }
            }
        }
        invalidate()
        return true
    }

    private fun handlePointerDown(pointerId: Int, x: Float, y: Float) {
        if (phase != PickerPhase.LOBBY) return
        val number = nextParticipantNumber++
        pointerToParticipant[pointerId] = number
        activeTouches[number] = Touch(x, y)
        spawnRipple(x, y, ColorPalette.colorFor(number - 1))
        pickerListener?.onLobbyUpdate(activeTouches.size)
        scheduleLockIn()
    }

    private fun handlePointerUp(pointerId: Int) {
        val number = pointerToParticipant.remove(pointerId) ?: return
        if (phase == PickerPhase.LOBBY) {
            activeTouches.remove(number)
            pickerListener?.onLobbyUpdate(activeTouches.size)
            if (activeTouches.size < 2) {
                lockInRunnable?.let { handler.removeCallbacks(it) }
            }
        }
        // Once locked in, lifting a finger is harmless: its last known
        // position was already captured and the pool is fixed for this spin.
    }

    private fun scheduleLockIn() {
        lockInRunnable?.let { handler.removeCallbacks(it) }
        if (activeTouches.size < 2) return
        val runnable = Runnable { beginPick() }
        lockInRunnable = runnable
        handler.postDelayed(runnable, LOCK_IN_DELAY_MS)
    }

    private fun beginPick() {
        if (phase != PickerPhase.LOBBY || activeTouches.size < 2) return
        phase = PickerPhase.SPINNING
        if (mode == PickType.TEAM_SPLIT) {
            beginTeamSplit()
        } else {
            runSpin(activeTouches.keys.toList())
        }
    }

    private fun runSpin(candidates: List<Int>) {
        val winner = candidates.random()
        winnerParticipant = winner
        var stepsLeft = SPIN_STEPS
        var delay = SPIN_START_DELAY_MS

        fun step() {
            if (stepsLeft <= 0) {
                highlightedParticipant = winner
                invalidate()
                onSpinFinished(winner)
                return
            }
            highlightedParticipant = candidates.random()
            invalidate()
            stepsLeft--
            delay = (delay * SPIN_SLOWDOWN).toLong().coerceAtLeast(40L)
            val runnable = Runnable { step() }
            spinStepRunnable = runnable
            handler.postDelayed(runnable, delay)
        }
        step()
    }

    private fun onSpinFinished(winner: Int) {
        phase = PickerPhase.REVEALED
        soundManager?.playRoundWin()
        winnerParticipant = winner
        activeTouches[winner]?.let { spawnConfettiBurst(it.x, it.y) }
        val remainingAfterThis = activeTouches.size - 1
        val isFinal = mode != PickType.ORDER || remainingAfterThis <= 1
        pickerListener?.onPickRevealed(winner, remainingAfterThis, isFinal)

        if (mode != PickType.ORDER) {
            phase = PickerPhase.DONE
            pickerListener?.onAllDone()
            return
        }

        val runnable = Runnable { advanceElimination(winner) }
        advanceRunnable = runnable
        handler.postDelayed(runnable, REVEAL_PAUSE_MS)
    }

    private fun advanceElimination(justPicked: Int) {
        activeTouches.remove(justPicked)
        when {
            activeTouches.size == 1 -> {
                val last = activeTouches.keys.first()
                winnerParticipant = last
                activeTouches.remove(last)
                pickerListener?.onPickRevealed(last, 0, true)
                phase = PickerPhase.DONE
                pickerListener?.onAllDone()
            }
            activeTouches.size >= 2 -> {
                phase = PickerPhase.SPINNING
                runSpin(activeTouches.keys.toList())
            }
            else -> {
                phase = PickerPhase.DONE
                pickerListener?.onAllDone()
            }
        }
        invalidate()
    }

    private fun beginTeamSplit() {
        val participants = activeTouches.keys.shuffled()
        val teamA = mutableListOf<Int>()
        val teamB = mutableListOf<Int>()
        participants.forEachIndexed { index, number ->
            if (index % 2 == 0) teamA.add(number) else teamB.add(number)
        }
        teamAssignment.clear()
        teamA.forEach { teamAssignment[it] = 0 }
        teamB.forEach { teamAssignment[it] = 1 }

        var stepsLeft = SPIN_STEPS

        fun step() {
            if (stepsLeft <= 0) {
                phase = PickerPhase.REVEALED
                soundManager?.playRoundWin()
                spawnConfettiBurst(width / 2f, height / 2f)
                pickerListener?.onTeamsAssigned(teamA, teamB)
                phase = PickerPhase.DONE
                pickerListener?.onAllDone()
                invalidate()
                return
            }
            for (number in participants) {
                flickerTeam[number] = Random.nextInt(2)
            }
            invalidate()
            stepsLeft--
            val runnable = Runnable { step() }
            spinStepRunnable = runnable
            handler.postDelayed(runnable, TEAM_FLICKER_INTERVAL_MS)
        }
        step()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        drawRipples(canvas)
        for ((number, touch) in activeTouches) {
            val color = when {
                mode == PickType.TEAM_SPLIT && phase == PickerPhase.SPINNING ->
                    ColorPalette.colorForTeam(flickerTeam[number] ?: 0)
                mode == PickType.TEAM_SPLIT && phase != PickerPhase.LOBBY ->
                    ColorPalette.colorForTeam(teamAssignment[number] ?: 0)
                number == winnerParticipant && phase != PickerPhase.SPINNING -> ColorPalette.WINNER_COLOR
                phase == PickerPhase.SPINNING && number != highlightedParticipant -> ColorPalette.DIM_COLOR
                else -> ColorPalette.colorFor(number - 1)
            }
            val isHighlighted = mode != PickType.TEAM_SPLIT &&
                (number == highlightedParticipant || (number == winnerParticipant && phase != PickerPhase.SPINNING))
            val radius = if (isHighlighted) radiusPx * 1.2f else radiusPx
            drawPlayerCircle(canvas, touch.x, touch.y, radius, color, number.toString())
        }
        drawConfetti(canvas)
    }

    companion object {
        private const val LOCK_IN_DELAY_MS = 1200L
        private const val REVEAL_PAUSE_MS = 1600L
        private const val SPIN_STEPS = 14
        private const val SPIN_START_DELAY_MS = 70L
        private const val SPIN_SLOWDOWN = 1.22
        private const val TEAM_FLICKER_INTERVAL_MS = 90L
    }
}
