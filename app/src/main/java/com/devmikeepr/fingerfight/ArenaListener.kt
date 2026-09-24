package com.devmikeepr.fingerfight

interface ArenaListener {
    fun onPhaseChanged(phase: GamePhase, message: String)
    fun onTimerTick(secondsRemaining: Int)
    fun onRoundEnded(result: RoundResult)
}
