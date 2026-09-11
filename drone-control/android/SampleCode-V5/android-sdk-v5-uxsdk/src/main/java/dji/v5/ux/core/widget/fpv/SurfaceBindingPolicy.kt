package dji.v5.ux.core.widget.fpv

/** Pure lifecycle gate. A successful put is registration evidence, never rendered-pixel evidence. */
internal class SurfaceBindingPolicy {
    data class Descriptor(val surface: Any, val manager: Any, val camera: String,
                          val width: Int, val height: Int, val scale: String, val generation: Long) {
        fun same(other: Descriptor?) = other != null && surface === other.surface && manager === other.manager &&
            camera == other.camera && width == other.width && height == other.height &&
            scale == other.scale && generation == other.generation
    }
    var attached = false
        private set
    @Volatile var generation = 0L
        private set
    var surface: Any? = null
        private set
    var width = 0
        private set
    var height = 0
        private set
    var bound: Descriptor? = null
        private set
    private var failed: Descriptor? = null

    fun attach() { attached = true; generation++; failed = null }
    fun created(value: Any) { generation++; surface = value; width = 0; height = 0; failed = null }
    fun changed(value: Any, width: Int, height: Int) {
        if (surface !== value) generation++
        surface = value; this.width = width; this.height = height; failed = null
    }
    fun destroy() { generation++; surface = null; width = 0; height = 0; bound = null; failed = null }
    fun detach() { attached = false; destroy() }
    fun allowRetry() { failed = null }
    fun forgetBinding() { bound = null; failed = null }
    fun candidate(valid: Boolean, manager: Any?, camera: String, scale: String): Descriptor? {
        val current = surface ?: return null
        if (!attached || !valid || width <= 0 || height <= 0 || manager == null || camera == "UNKNOWN") return null
        return Descriptor(current, manager, camera, width, height, scale, generation)
    }
    fun needsPut(candidate: Descriptor, ownerHasBinding: Boolean): Boolean =
        !candidate.same(failed) && (!candidate.same(bound) || !ownerHasBinding)
    fun putResult(candidate: Descriptor, success: Boolean) {
        if (candidate.generation != generation || candidate.surface !== surface || !attached) return
        if (success) { bound = candidate; failed = null } else failed = candidate
    }
}

/** Owns the exact manager that accepted a Surface; independent of the current SDK singleton. */
internal class SurfaceBindingOwner<S : Any, M : Any>(private val remove: (M, S) -> Unit) {
    private var owner: M? = null
    private var surface: S? = null
    var lastError: String? = null
        private set
    var puts = 0L
        private set
    var successes = 0L
        private set
    var removes = 0L
        private set

    @Synchronized fun hasBinding(value: S, manager: M) = value === surface && manager === owner
    @Synchronized fun put(value: S, manager: M, effect: () -> Unit): Boolean {
        if (owner != null && (owner !== manager || surface !== value)) clear()
        puts++
        return try {
            effect()
            owner = manager; surface = value; successes++; lastError = null
            true
        } catch (error: RuntimeException) {
            lastError = "put: ${error.javaClass.simpleName}: ${error.message}"
            false
        }
    }
    @Synchronized fun clear(value: S? = null) {
        if (value != null && value !== surface) return
        val oldOwner = owner
        val oldSurface = surface
        owner = null; surface = null // always clear, even when SDK remove throws
        if (oldOwner != null && oldSurface != null) {
            removes++
            try { remove(oldOwner, oldSurface) }
            catch (error: RuntimeException) { lastError = "remove: ${error.javaClass.simpleName}: ${error.message}" }
        }
    }
}
