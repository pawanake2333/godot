extends RefCounted

const MAX_SNAPSHOTS := 8
const MAX_NODES := 16384
const MAX_BYTES := 2097152
const TTL_MS := 30000
var _snapshots := {}
var _order: Array[String] = []

func failure(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}

func read(root: Node, args: Dictionary, revision: String, codec: RefCounted, runtime: bool) -> Dictionary:
	var now := Time.get_ticks_msec()
	for key in _order.duplicate():
		if now - int(_snapshots[key].created) >= TTL_MS:
			_snapshots.erase(key)
			_order.erase(key)
	var snapshot_id := str(args.get("snapshot", ""))
	var offset := int(args.get("offset", 0))
	var selector := {"root_path": args.get("root_path", "."), "class_name": args.get("class_name", ""), "name_pattern": args.get("name_pattern", "*")}
	if snapshot_id.is_empty():
		if offset != 0:
			return failure("SNAPSHOT_REQUIRED", "Use the snapshot id returned by the first page")
		var base := root.get_node_or_null(NodePath(str(selector.root_path)))
		if base == null or (base != root and not root.is_ancestor_of(base)):
			return failure("OBJECT_NOT_FOUND", "Snapshot root is outside the selected scene")
		var queue: Array[Node] = [base]
		var examined := 0
		var bytes := 0
		var source_cut := false
		var entries: Array = []
		while examined < queue.size() and examined < MAX_NODES:
			var node := queue[examined]
			examined += 1
			if (str(selector.class_name).is_empty() or node.is_class(str(selector.class_name))) and str(node.name).match(str(selector.name_pattern)):
				var entry := {"path": str(node.get_path()) if runtime else str(root.get_path_to(node)), "name": str(node.name), "class": node.get_class(), "groups": node.get_groups(), "instance_path": node.scene_file_path, "owner_path": str(node.owner.get_path()) if runtime and node.owner != null else str(root.get_path_to(node.owner)) if node.owner != null and (node.owner == root or root.is_ancestor_of(node.owner)) else null}
				bytes += JSON.stringify(entry).to_utf8_buffer().size() + 64
				if bytes > MAX_BYTES:
					break
				entry["weak"] = weakref(node)
				entries.append(entry)
			var available := MAX_NODES - queue.size()
			if node.get_child_count() > available:
				source_cut = true
			for child_index in range(mini(node.get_child_count(), available)):
				queue.append(node.get_child(child_index))
		snapshot_id = Crypto.new().generate_random_bytes(16).hex_encode()
		if _order.size() >= MAX_SNAPSHOTS:
			_snapshots.erase(_order.pop_front())
		_order.append(snapshot_id)
		_snapshots[snapshot_id] = {"created": now, "root": weakref(root), "revision": revision, "selector": selector, "entries": entries, "source_truncated": source_cut or examined < queue.size() or bytes > MAX_BYTES, "runtime": runtime}
	if not _snapshots.has(snapshot_id):
		return failure("SNAPSHOT_EXPIRED", "Snapshot expired or belongs to another editor/run context")
	var snapshot: Dictionary = _snapshots[snapshot_id]
	if snapshot.root.get_ref() != root or snapshot.selector != selector or snapshot.runtime != runtime:
		return failure("SNAPSHOT_CONFLICT", "Snapshot target or filter differs from the first page")
	if not runtime and snapshot.revision != revision:
		return failure("SNAPSHOT_CHANGED", "Scene document changed; start a new snapshot")
	if offset > snapshot.entries.size():
		return failure("INVALID_CURSOR", "Offset is beyond the snapshot")
	var limit := int(args.get("limit", 128))
	var result: Array = []
	for stored in snapshot.entries.slice(offset, offset + limit):
		var entry: Dictionary = stored.duplicate()
		var node: Node = entry.weak.get_ref()
		entry.erase("weak")
		entry["expired"] = node == null
		entry["object"] = codec.retain_object(node) if node != null else null
		result.append(entry)
	return {"ok": true, "result": {"nodes": result, "snapshot": snapshot_id, "next_offset": offset + result.size(), "total": snapshot.entries.size(), "has_more": offset + result.size() < snapshot.entries.size(), "truncated": snapshot.source_truncated or offset + result.size() < snapshot.entries.size(), "source_truncated": snapshot.source_truncated, "snapshot_revision": snapshot.revision, "snapshot_created_ms": snapshot.created, "snapshot_ttl_ms": TTL_MS}}
