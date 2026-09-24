package com.devmikeepr.fingerfight

object ColorPalette {
    private val PLAYER_COLORS = intArrayOf(
        0xFFE53935.toInt(), // Player 1 - Red
        0xFF1E88E5.toInt(), // Player 2 - Blue
        0xFF43A047.toInt(), // Player 3 - Green
        0xFFFDD835.toInt(), // Player 4 - Amber
        0xFF8E24AA.toInt(), // Player 5 - Purple
        0xFFFB8C00.toInt()  // Player 6 - Orange
    )

    const val HUNTER_COLOR = 0xFFFF1744.toInt()

    fun colorFor(slotIndex: Int): Int = PLAYER_COLORS[slotIndex % PLAYER_COLORS.size]
}
