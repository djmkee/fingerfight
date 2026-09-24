package com.devmikeepr.fingerfight

object ColorPalette {
    private val PLAYER_COLORS = intArrayOf(
        0xFFE53935.toInt(), // Player 1 - Red
        0xFF1E88E5.toInt(), // Player 2 - Blue
        0xFF43A047.toInt(), // Player 3 - Green
        0xFFFDD835.toInt(), // Player 4 - Amber
        0xFF8E24AA.toInt(), // Player 5 - Purple
        0xFFFB8C00.toInt(), // Player 6 - Orange
        0xFF00ACC1.toInt(), // Player 7 - Teal
        0xFFD81B60.toInt()  // Player 8 - Pink
    )

    const val WINNER_COLOR = 0xFF00E676.toInt()
    const val DIM_COLOR = 0xFF3A3A50.toInt()
    const val TEAM_A_COLOR = 0xFF00E5FF.toInt()
    const val TEAM_B_COLOR = 0xFFFF4081.toInt()

    fun colorFor(slotIndex: Int): Int = PLAYER_COLORS[slotIndex % PLAYER_COLORS.size]

    fun colorForTeam(teamIndex: Int): Int = if (teamIndex == 0) TEAM_A_COLOR else TEAM_B_COLOR
}
