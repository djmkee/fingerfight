package com.devmikeepr.fingerfight

/** Per-mode color/icon identity so setup and how-to-play screens don't all look identical. */
object ModeStyle {

    fun accentColorRes(mode: DuelMode): Int = when (mode) {
        DuelMode.REACTION -> R.color.accent
        DuelMode.REACTION_RUMBLE -> R.color.accent_rumble
        DuelMode.TAP_BATTLE -> R.color.accent_tapbattle
        DuelMode.ENDURANCE -> R.color.accent_endurance
    }

    fun heroBackgroundRes(mode: DuelMode): Int = when (mode) {
        DuelMode.REACTION -> R.drawable.bg_card_reaction
        DuelMode.REACTION_RUMBLE -> R.drawable.bg_card_rumble
        DuelMode.TAP_BATTLE -> R.drawable.bg_card_tapbattle
        DuelMode.ENDURANCE -> R.drawable.bg_card_endurance
    }

    fun iconRes(mode: DuelMode): Int = when (mode) {
        DuelMode.REACTION -> R.drawable.ic_bolt
        DuelMode.REACTION_RUMBLE -> R.drawable.ic_bolt_group
        DuelMode.TAP_BATTLE -> R.drawable.ic_tap
        DuelMode.ENDURANCE -> R.drawable.ic_hourglass
    }

    val pickerAccentRes: Int = R.color.accent_picker
    val pickerHeroBackgroundRes: Int = R.drawable.bg_card_picker
    val pickerIconRes: Int = R.drawable.ic_touch_rings
}
