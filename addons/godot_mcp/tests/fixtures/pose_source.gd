@tool
extends Node3D

static var constructions := 0
var construction_total: int:
	get:
		return constructions
@export var calls := 0

func _init() -> void:
	constructions += 1

func construction_count() -> int:
	return constructions

func method_track_probe() -> void:
	calls += 1
