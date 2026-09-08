package tasks

import (
	"reflect"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func cloneTelemetrySnapshot(snapshot telemetry.Snapshot) telemetry.Snapshot {
	snapshot.Anomalies = append([]string(nil), snapshot.Anomalies...)
	snapshot.Entities = append([]telemetry.Entity(nil), snapshot.Entities...)
	for index := range snapshot.Entities {
		entity := &snapshot.Entities[index]
		entity.Attributes = cloneStrings(entity.Attributes)
		entity.Pose = append([]float64(nil), entity.Pose...)
	}
	if snapshot.RobotState != nil {
		snapshot.RobotState = cloneTelemetryValue(reflect.ValueOf(snapshot.RobotState)).Interface().(map[string]any)
	}
	if source := snapshot.Reconstruction; source != nil {
		copy := *source
		copy.Entities = append([]robotcontract.Entity(nil), source.Entities...)
		for index := range copy.Entities {
			copy.Entities[index].Attributes = cloneStrings(copy.Entities[index].Attributes)
			copy.Entities[index].Pose = append([]float64(nil), copy.Entities[index].Pose...)
		}
		copy.Points = make([][]float64, len(source.Points))
		for index := range source.Points {
			copy.Points[index] = append([]float64(nil), source.Points[index]...)
		}
		copy.PointColors = make([][]int, len(source.PointColors))
		for index := range source.PointColors {
			copy.PointColors[index] = append([]int(nil), source.PointColors[index]...)
		}
		snapshot.Reconstruction = &copy
	}
	if source := snapshot.RobotProfile; source != nil {
		copy := *source
		copy.Joints = append([]robotcontract.Joint(nil), source.Joints...)
		copy.EndEffectors = append([]robotcontract.EndEffector(nil), source.EndEffectors...)
		for index := range copy.EndEffectors {
			copy.EndEffectors[index].JointNames = append([]string(nil), source.EndEffectors[index].JointNames...)
		}
		copy.Sensors = append([]robotcontract.Sensor(nil), source.Sensors...)
		copy.Tools = append([]string(nil), source.Tools...)
		if source.ActionLimits != nil {
			copy.ActionLimits = make(map[string]robotcontract.ActionLimit, len(source.ActionLimits))
			for name, limit := range source.ActionLimits {
				copy.ActionLimits[name] = limit
			}
		}
		snapshot.RobotProfile = &copy
	}
	return snapshot
}

// RobotState comes from protobuf JSON. Preserve numeric slice/map types used
// by in-process producers while recursively owning their mutable contents.
func cloneTelemetryValue(value reflect.Value) reflect.Value {
	switch value.Kind() {
	case reflect.Interface:
		if value.IsNil() {
			return value
		}
		copy := reflect.New(value.Type()).Elem()
		copy.Set(cloneTelemetryValue(value.Elem()))
		return copy
	case reflect.Map:
		if value.IsNil() {
			return value
		}
		copy := reflect.MakeMapWithSize(value.Type(), value.Len())
		iterator := value.MapRange()
		for iterator.Next() {
			copy.SetMapIndex(iterator.Key(), cloneTelemetryValue(iterator.Value()))
		}
		return copy
	case reflect.Slice:
		if value.IsNil() {
			return value
		}
		copy := reflect.MakeSlice(value.Type(), value.Len(), value.Len())
		for index := 0; index < value.Len(); index++ {
			copy.Index(index).Set(cloneTelemetryValue(value.Index(index)))
		}
		return copy
	default:
		return value
	}
}
