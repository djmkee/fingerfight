package com.devmikeepr.fingerfight

interface PickerListener {
    /** Called whenever the number of fingers held down during the lobby changes. */
    fun onLobbyUpdate(fingerCount: Int)

    /**
     * Called each time a finger is picked. [remaining] is how many are still
     * in the pool afterwards (only meaningful in elimination/order mode).
     * [isFinalPick] is true once there is nothing left to pick.
     */
    fun onPickRevealed(participantNumber: Int, remaining: Int, isFinalPick: Boolean)

    /** Called once the whole pick (single winner, or full order) is finished. */
    fun onAllDone()
}
