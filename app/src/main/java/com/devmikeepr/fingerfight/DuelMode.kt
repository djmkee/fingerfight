package com.devmikeepr.fingerfight

enum class DuelMode(val minPlayers: Int, val maxPlayers: Int) {
    REACTION(2, 2),
    TAP_BATTLE(2, 2),
    ENDURANCE(2, 8),
    REACTION_RUMBLE(2, 8)
}
