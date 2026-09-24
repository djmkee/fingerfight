package com.devmikeepr.fingerfight

/**
 * [pointsAwarded] maps a player slot index to the points earned this round.
 * [summary] is a short human-readable description of what happened.
 */
data class RoundResult(
    val pointsAwarded: Map<Int, Int>,
    val summary: String
)
