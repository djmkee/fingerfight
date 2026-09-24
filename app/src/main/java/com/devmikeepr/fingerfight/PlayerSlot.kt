package com.devmikeepr.fingerfight

data class PlayerSlot(
    val index: Int,
    val color: Int,
    var totalScore: Int = 0
)
