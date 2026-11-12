/**************************************************************************/
/*  editor_debugger_plugin.cpp                                            */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/

#include "editor_debugger_plugin.h"

#include "core/object/callable_mp.h"
#include "core/object/class_db.h"
#include "editor/debugger/script_editor_debugger.h"

void EditorDebuggerSession::_breaked(bool p_really_did, bool p_can_debug, const String &p_message, bool p_has_stackdump) {
	if (p_really_did) {
		emit_signal(SNAME("breaked"), p_can_debug);
		emit_signal(SNAME("break_reason"), p_message);
	} else {
		emit_signal(SNAME("continued"));
	}
}

void EditorDebuggerSession::_profiler_frame(const String &p_kind, const Array &p_data) {
	emit_signal(SNAME("profiler_frame"), p_kind, p_data);
}

void EditorDebuggerSession::_stack_dump(const Array &p_data) {
	emit_signal(SNAME("stack_dump"), p_data);
}

void EditorDebuggerSession::_stack_frame_vars(int p_count) {
	emit_signal(SNAME("stack_frame_variables_started"), p_count);
}

void EditorDebuggerSession::_stack_frame_var(const Array &p_data) {
	emit_signal(SNAME("stack_frame_variable"), p_data);
}

Error EditorDebuggerSession::debug_control(const String &p_action) {
	ERR_FAIL_NULL_V(debugger, ERR_UNCONFIGURED);
	if (!debugger->is_session_active()) {
		return ERR_UNAVAILABLE;
	}
	if (p_action == "pause") {
		if (debugger->is_breaked()) {
			return ERR_BUSY;
		}
		debugger->debug_break();
		return OK;
	}
	if (!debugger->is_breaked()) {
		return ERR_BUSY;
	}
	if (p_action == "continue") {
		debugger->debug_continue();
	} else if (p_action == "step_into") {
		debugger->debug_step();
	} else if (p_action == "step_over") {
		debugger->debug_next();
	} else if (p_action == "step_out") {
		debugger->debug_out();
	} else {
		return ERR_INVALID_PARAMETER;
	}
	return OK;
}

bool EditorDebuggerSession::request_stack_variables(int p_frame) {
	ERR_FAIL_NULL_V(debugger, false);
	if (!debugger->is_breaked()) {
		return false;
	}
	return debugger->request_stack_dump(p_frame);
}

void EditorDebuggerSession::_started() {
	emit_signal(SNAME("started"));
}

void EditorDebuggerSession::_stopped() {
	emit_signal(SNAME("stopped"));
}

void EditorDebuggerSession::_bind_methods() {
	ClassDB::bind_method(D_METHOD("send_message", "message", "data"), &EditorDebuggerSession::send_message, DEFVAL(Array()));
	ClassDB::bind_method(D_METHOD("toggle_profiler", "profiler", "enable", "data"), &EditorDebuggerSession::toggle_profiler, DEFVAL(Array()));
	ClassDB::bind_method(D_METHOD("get_profiler_state", "profiler"), &EditorDebuggerSession::get_profiler_state);
	ClassDB::bind_method(D_METHOD("is_breaked"), &EditorDebuggerSession::is_breaked);
	ClassDB::bind_method(D_METHOD("is_debuggable"), &EditorDebuggerSession::is_debuggable);
	ClassDB::bind_method(D_METHOD("is_active"), &EditorDebuggerSession::is_active);
	ClassDB::bind_method(D_METHOD("add_session_tab", "control"), &EditorDebuggerSession::add_session_tab);
	ClassDB::bind_method(D_METHOD("remove_session_tab", "control"), &EditorDebuggerSession::remove_session_tab);
	ClassDB::bind_method(D_METHOD("set_breakpoint", "path", "line", "enabled"), &EditorDebuggerSession::set_breakpoint);

	ClassDB::bind_method(D_METHOD("debug_control", "action"), &EditorDebuggerSession::debug_control);
	ClassDB::bind_method(D_METHOD("request_stack_variables", "frame"), &EditorDebuggerSession::request_stack_variables);
	ADD_SIGNAL(MethodInfo("profiler_frame", PropertyInfo(Variant::STRING, "kind"), PropertyInfo(Variant::ARRAY, "data")));
	ADD_SIGNAL(MethodInfo("stack_dump", PropertyInfo(Variant::ARRAY, "frames")));
	ADD_SIGNAL(MethodInfo("stack_frame_variables_started", PropertyInfo(Variant::INT, "count")));
	ADD_SIGNAL(MethodInfo("stack_frame_variable", PropertyInfo(Variant::ARRAY, "data")));
	ADD_SIGNAL(MethodInfo("break_reason", PropertyInfo(Variant::STRING, "message")));
	ADD_SIGNAL(MethodInfo("started"));
	ADD_SIGNAL(MethodInfo("stopped"));
	ADD_SIGNAL(MethodInfo("breaked", PropertyInfo(Variant::BOOL, "can_debug")));
	ADD_SIGNAL(MethodInfo("continued"));
}

void EditorDebuggerSession::add_session_tab(Control *p_tab) {
	ERR_FAIL_COND(!p_tab || !debugger);
	debugger->add_debugger_tab(p_tab);
	tabs.insert(p_tab);
}

void EditorDebuggerSession::remove_session_tab(Control *p_tab) {
	ERR_FAIL_COND(!p_tab || !debugger);
	debugger->remove_debugger_tab(p_tab);
	tabs.erase(p_tab);
}

void EditorDebuggerSession::send_message(const String &p_message, const Array &p_args) {
	ERR_FAIL_NULL_MSG(debugger, "Plugin is not attached to debugger.");
	debugger->send_message(p_message, p_args);
}

void EditorDebuggerSession::toggle_profiler(const String &p_profiler, bool p_enable, const Array &p_data) {
	ERR_FAIL_NULL_MSG(debugger, "Plugin is not attached to debugger.");
	debugger->toggle_profiler(p_profiler, p_enable, p_data);
}

Dictionary EditorDebuggerSession::get_profiler_state(const String &p_profiler) const {
	ERR_FAIL_NULL_V(debugger, Dictionary());
	return debugger->get_profiler_state(p_profiler);
}

bool EditorDebuggerSession::is_breaked() {
	ERR_FAIL_NULL_V_MSG(debugger, false, "Plugin is not attached to debugger.");
	return debugger->is_breaked();
}

bool EditorDebuggerSession::is_debuggable() {
	ERR_FAIL_NULL_V_MSG(debugger, false, "Plugin is not attached to debugger.");
	return debugger->is_debuggable();
}

bool EditorDebuggerSession::is_active() {
	ERR_FAIL_NULL_V_MSG(debugger, false, "Plugin is not attached to debugger.");
	return debugger->is_session_active();
}

void EditorDebuggerSession::set_breakpoint(const String &p_path, int p_line, bool p_enabled) {
	ERR_FAIL_NULL_MSG(debugger, "Plugin is not attached to debugger.");
	debugger->set_breakpoint(p_path, p_line, p_enabled);
}

void EditorDebuggerSession::detach_debugger() {
	if (!debugger || !ObjectDB::get_instance(debugger_id)) {
		return;
	}
	debugger->disconnect("profiler_frame", callable_mp(this, &EditorDebuggerSession::_profiler_frame));
	debugger->disconnect("stack_dump", callable_mp(this, &EditorDebuggerSession::_stack_dump));
	debugger->disconnect("stack_frame_vars", callable_mp(this, &EditorDebuggerSession::_stack_frame_vars));
	debugger->disconnect("stack_frame_var", callable_mp(this, &EditorDebuggerSession::_stack_frame_var));
	debugger->disconnect("started", callable_mp(this, &EditorDebuggerSession::_started));
	debugger->disconnect("stopped", callable_mp(this, &EditorDebuggerSession::_stopped));
	debugger->disconnect("breaked", callable_mp(this, &EditorDebuggerSession::_breaked));
	for (Control *tab : tabs) {
		debugger->remove_debugger_tab(tab);
	}
	tabs.clear();
	debugger = nullptr;
}

EditorDebuggerSession::EditorDebuggerSession(ScriptEditorDebugger *p_debugger) {
	ERR_FAIL_NULL(p_debugger);
	debugger = p_debugger;
	debugger_id = p_debugger->get_instance_id();
	debugger->connect("profiler_frame", callable_mp(this, &EditorDebuggerSession::_profiler_frame));
	debugger->connect("stack_dump", callable_mp(this, &EditorDebuggerSession::_stack_dump));
	debugger->connect("stack_frame_vars", callable_mp(this, &EditorDebuggerSession::_stack_frame_vars));
	debugger->connect("stack_frame_var", callable_mp(this, &EditorDebuggerSession::_stack_frame_var));
	debugger->connect("started", callable_mp(this, &EditorDebuggerSession::_started));
	debugger->connect("stopped", callable_mp(this, &EditorDebuggerSession::_stopped));
	debugger->connect("breaked", callable_mp(this, &EditorDebuggerSession::_breaked));
}

EditorDebuggerSession::~EditorDebuggerSession() {
	detach_debugger();
}

/// EditorDebuggerPlugin

EditorDebuggerPlugin::~EditorDebuggerPlugin() {
	clear();
}

void EditorDebuggerPlugin::clear() {
	for (Ref<EditorDebuggerSession> &session : sessions) {
		session->detach_debugger();
	}
	sessions.clear();
}

void EditorDebuggerPlugin::create_session(ScriptEditorDebugger *p_debugger) {
	sessions.push_back(Ref<EditorDebuggerSession>(memnew(EditorDebuggerSession(p_debugger))));
	setup_session(sessions.size() - 1);
}

void EditorDebuggerPlugin::setup_session(int p_idx) {
	GDVIRTUAL_CALL(_setup_session, p_idx);
}

Ref<EditorDebuggerSession> EditorDebuggerPlugin::get_session(int p_idx) {
	ERR_FAIL_INDEX_V(p_idx, sessions.size(), nullptr);
	return sessions.get(p_idx);
}

Array EditorDebuggerPlugin::get_sessions() {
	Array ret;
	for (const Ref<EditorDebuggerSession> &session : sessions) {
		ret.push_back(session);
	}
	return ret;
}

bool EditorDebuggerPlugin::has_capture(const String &p_message) const {
	bool ret = false;
	if (GDVIRTUAL_CALL(_has_capture, p_message, ret)) {
		return ret;
	}
	return false;
}

bool EditorDebuggerPlugin::capture(const String &p_message, const Array &p_data, int p_session_id) {
	bool ret = false;
	if (GDVIRTUAL_CALL(_capture, p_message, p_data, p_session_id, ret)) {
		return ret;
	}
	return false;
}

void EditorDebuggerPlugin::goto_script_line(const Ref<Script> &p_script, int p_line) {
	GDVIRTUAL_CALL(_goto_script_line, p_script, p_line);
}

void EditorDebuggerPlugin::breakpoints_cleared_in_tree() {
	GDVIRTUAL_CALL(_breakpoints_cleared_in_tree);
}

void EditorDebuggerPlugin::breakpoint_set_in_tree(const Ref<Script> &p_script, int p_line, bool p_enabled) {
	GDVIRTUAL_CALL(_breakpoint_set_in_tree, p_script, p_line, p_enabled);
}

void EditorDebuggerPlugin::_bind_methods() {
	GDVIRTUAL_BIND(_setup_session, "session_id");
	GDVIRTUAL_BIND(_has_capture, "capture");
	GDVIRTUAL_BIND(_capture, "message", "data", "session_id");
	GDVIRTUAL_BIND(_goto_script_line, "script", "line");
	GDVIRTUAL_BIND(_breakpoints_cleared_in_tree);
	GDVIRTUAL_BIND(_breakpoint_set_in_tree, "script", "line", "enabled");
	ClassDB::bind_method(D_METHOD("get_session", "id"), &EditorDebuggerPlugin::get_session);
	ClassDB::bind_method(D_METHOD("get_sessions"), &EditorDebuggerPlugin::get_sessions);
}

EditorDebuggerPlugin::EditorDebuggerPlugin() {
	EditorDebuggerNode::get_singleton()->connect("goto_script_line", callable_mp(this, &EditorDebuggerPlugin::goto_script_line));
	EditorDebuggerNode::get_singleton()->connect("breakpoints_cleared_in_tree", callable_mp(this, &EditorDebuggerPlugin::breakpoints_cleared_in_tree));
	EditorDebuggerNode::get_singleton()->connect("breakpoint_set_in_tree", callable_mp(this, &EditorDebuggerPlugin::breakpoint_set_in_tree));
}
