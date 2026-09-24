package com.devmikeepr.fingerfight

enum class GameMode(val minPlayers: Int, val maxPlayers: Int) {
    CHASE(2, 6),
    REACTION(2, 2)
}
