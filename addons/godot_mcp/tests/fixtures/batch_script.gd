extends Node3D

signal marked(amount: int)
@export var value := 1
@export var captured := 0

func trigger_marker() -> void:
	marked.emit(value)

func receive_marker(amount: int, bound: int) -> void:
	captured = amount + bound
