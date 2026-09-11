package dji.v5.ux.core.widget.fpv

import org.junit.Assert.*
import org.junit.Test

class SurfaceBindingPolicyTest {
    private val policy = SurfaceBindingPolicy()
    private val surface = Any()
    private val manager = Any()
    private fun candidate(valid: Boolean = true, camera: String = "LEFT_OR_MAIN") =
        policy.candidate(valid, manager, camera, "CENTER_INSIDE")
    private fun ready() { policy.attach(); policy.created(surface); policy.changed(surface, 640, 360) }

    @Test fun creationWaitsForValidPositiveDimensionsAndKnownCamera() {
        policy.attach(); policy.created(surface)
        assertNull(candidate())
        policy.changed(surface, 0, 360); assertNull(candidate())
        policy.changed(surface, 640, 360)
        assertNull(candidate(false)); assertNull(candidate(camera = "UNKNOWN")); assertNotNull(candidate())
    }
    @Test fun destroyClearsReferenceDimensionsDescriptorAndOldPostedGeneration() {
        ready(); val descriptor = candidate()!!; policy.putResult(descriptor, true)
        val oldGeneration = policy.generation; policy.destroy()
        assertNull(policy.surface); assertNull(policy.bound); assertEquals(0, policy.width); assertEquals(0, policy.height)
        assertTrue(policy.generation > oldGeneration); assertNull(candidate())
        policy.putResult(descriptor, true); assertNull(policy.bound)
    }
    @Test fun duplicateDescriptorsSkipPutAndResizeCameraChangesUsePut() {
        ready(); val first = candidate()!!; policy.putResult(first, true)
        assertFalse(policy.needsPut(candidate()!!, true))
        policy.changed(surface, 1280, 720); assertTrue(policy.needsPut(candidate()!!, true))
        assertTrue(policy.needsPut(candidate(camera = "FPV")!!, true))
        assertTrue(policy.needsPut(first, false))
    }
    @Test fun detachedWidgetMustAcquireItsValidSurfaceAgain() {
        ready(); policy.putResult(candidate()!!, true); policy.detach()
        assertFalse(policy.attached); assertNull(candidate()); assertNull(policy.surface)
        policy.attach(); assertNull(candidate())
        policy.created(surface); assertNull(candidate())
        policy.changed(surface, 640, 360); assertTrue(policy.needsPut(candidate()!!, false))
    }
    @Test fun putFailureCannotBecomeSuccessfulOrRetryOnEveryModelUpdate() {
        ready(); val first = candidate()!!; policy.putResult(first, false)
        assertNull(policy.bound); assertFalse(policy.needsPut(first, false))
        policy.changed(surface, 640, 360); assertTrue(policy.needsPut(candidate()!!, false))
    }
    @Test fun managerIdentityChangeRequiresANewBinding() {
        ready(); policy.putResult(candidate()!!, true)
        val next = policy.candidate(true, Any(), "LEFT_OR_MAIN", "CENTER_INSIDE")!!
        assertTrue(policy.needsPut(next, true))
    }
    @Test fun ownerUsesTheOriginalManagerAndClearsEvenWhenRemoveThrows() {
        val removes = mutableListOf<Pair<Any, Any>>()
        val owner = SurfaceBindingOwner<Any, Any> { oldManager, oldSurface ->
            removes.add(Pair(oldManager, oldSurface)); throw IllegalStateException("SDK remove failed")
        }
        assertTrue(owner.put(surface, manager) { })
        val replacement = Any()
        assertTrue(owner.put(surface, replacement) { })
        assertSame(manager, removes[0].first); assertTrue(owner.hasBinding(surface, replacement))
        owner.clear(); owner.clear()
        assertEquals(2, removes.size); assertSame(replacement, removes[1].first)
        assertFalse(owner.hasBinding(surface, replacement))
    }
    @Test fun sameOwnerResizeUpdatesWithoutRemoveAndFailedPutNeverRecordsNewOwner() {
        var removed = 0; val owner = SurfaceBindingOwner<Any, Any> { _, _ -> removed++ }
        assertTrue(owner.put(surface, manager) { })
        assertTrue(owner.put(surface, manager) { }); assertEquals(0, removed)
        val replacement = Any()
        assertFalse(owner.put(surface, replacement) { throw IllegalStateException("put failed") })
        assertEquals(1, removed); assertFalse(owner.hasBinding(surface, replacement))
        assertFalse(owner.hasBinding(surface, manager)); owner.clear(); assertEquals(1, removed)
        assertEquals(3L, owner.puts); assertEquals(2L, owner.successes)
    }
}
