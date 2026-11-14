#include "gdextension_interface.gen.h"

#include <cstdint>

#ifndef PROBE_GENERATION
#define PROBE_GENERATION 1
#endif

namespace {
GDExtensionClassLibraryPtr library;
GDExtensionInterfaceStringNameNewWithUtf8Chars make_name;
GDExtensionPtrDestructor destroy_name;
GDExtensionInterfaceClassdbRegisterExtensionClass6 register_class;
GDExtensionInterfaceClassdbRegisterExtensionClassMethod register_method;
GDExtensionInterfaceClassdbUnregisterExtensionClass unregister_class;

struct Name {
	uintptr_t storage = 0;
	explicit Name(const char *text) { make_name(&storage, text); }
	~Name() { destroy_name(&storage); }
};

struct ThreadState {
	volatile int generation = 0;
	~ThreadState() { generation = 0; }
};

void call(void *, GDExtensionClassInstancePtr, const GDExtensionConstVariantPtr *, GDExtensionInt, GDExtensionVariantPtr, GDExtensionCallError *error) {
	error->error = GDEXTENSION_CALL_OK;
}

void initialize(void *, GDExtensionInitializationLevel level) {
	if (level != GDEXTENSION_INITIALIZATION_SCENE) {
		return;
	}
	// Match godot-cpp's per-image registration guard. A retained old image must
	// not silently produce an empty method table on the next load.
	static bool initialized = false;
	if (initialized) {
		return;
	}
	initialized = true;
	thread_local ThreadState state;
	state.generation = PROBE_GENERATION;
	Name name("MCPReloadProbe"), parent("RefCounted");
	GDExtensionClassCreationInfo6 info = {};
	info.is_abstract = true;
	info.is_exposed = true;
	info.free_instance_func = [](void *, GDExtensionClassInstancePtr) {};
	register_class(library, &name.storage, &parent.storage, &info);
	Name method(PROBE_GENERATION == 1 ? "generation_one" : "generation_two");
	GDExtensionClassMethodInfo binding = {};
	binding.name = &method.storage;
	binding.call_func = call;
	binding.method_flags = GDEXTENSION_METHOD_FLAG_NORMAL | GDEXTENSION_METHOD_FLAG_CONST;
	register_method(library, &name.storage, &binding);
}

void deinitialize(void *, GDExtensionInitializationLevel level) {
	if (level == GDEXTENSION_INITIALIZATION_SCENE) {
		Name name("MCPReloadProbe");
		unregister_class(library, &name.storage);
	}
}
} // namespace

#ifdef _WIN32
#define PROBE_EXPORT __declspec(dllexport)
#else
#define PROBE_EXPORT __attribute__((visibility("default")))
#endif

extern "C" PROBE_EXPORT GDExtensionBool mcp_reload_probe_init(GDExtensionInterfaceGetProcAddress get_proc, GDExtensionClassLibraryPtr instance, GDExtensionInitialization *initialization) {
	library = instance;
	make_name = reinterpret_cast<GDExtensionInterfaceStringNameNewWithUtf8Chars>(get_proc("string_name_new_with_utf8_chars"));
	auto get_destructor = reinterpret_cast<GDExtensionInterfaceVariantGetPtrDestructor>(get_proc("variant_get_ptr_destructor"));
	destroy_name = get_destructor(GDEXTENSION_VARIANT_TYPE_STRING_NAME);
	register_class = reinterpret_cast<GDExtensionInterfaceClassdbRegisterExtensionClass6>(get_proc("classdb_register_extension_class6"));
	register_method = reinterpret_cast<GDExtensionInterfaceClassdbRegisterExtensionClassMethod>(get_proc("classdb_register_extension_class_method"));
	unregister_class = reinterpret_cast<GDExtensionInterfaceClassdbUnregisterExtensionClass>(get_proc("classdb_unregister_extension_class"));
	initialization->minimum_initialization_level = GDEXTENSION_INITIALIZATION_SCENE;
	initialization->initialize = initialize;
	initialization->deinitialize = deinitialize;
	return true;
}
