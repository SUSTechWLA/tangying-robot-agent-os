package capability

import (
	"fmt"
	"math"
	"reflect"
	"unicode/utf8"
)

// ValidateSchema rejects keywords the executor cannot enforce. Schemas shown to
// the model and schemas checked before dispatch use this same bounded subset.
func ValidateSchema(schema map[string]any) error { return schemaAt(schema, 0) }
func schemaAt(s map[string]any, depth int) error {
	if depth > 20 || s == nil {
		return fmt.Errorf("invalid or excessively nested schema")
	}
	for key := range s {
		switch key {
		case "type", "properties", "items", "required", "additionalProperties", "enum", "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "description", "title":
		default:
			return fmt.Errorf("unsupported schema keyword %s", key)
		}
	}
	kind, _ := s["type"].(string)
	if _, exists := s["type"]; exists && kind == "" {
		return fmt.Errorf("type must be a nonempty string")
	}
	switch kind {
	case "object", "array", "string", "number", "integer", "boolean", "null":
	case "":
	default:
		return fmt.Errorf("unsupported type %s", kind)
	}
	for _, key := range []string{"minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"} {
		if value, exists := s[key]; exists {
			n, valid := numeric(value)
			if !valid || (key != "minimum" && key != "maximum" && (n < 0 || math.Trunc(n) != n)) {
				return fmt.Errorf("invalid schema constraint %s", key)
			}
		}
	}
	for _, pair := range [][2]string{{"minimum", "maximum"}, {"minItems", "maxItems"}, {"minLength", "maxLength"}} {
		lo, l := numeric(s[pair[0]])
		hi, h := numeric(s[pair[1]])
		if l && h && lo > hi {
			return fmt.Errorf("inverted schema bounds")
		}
	}
	if value, exists := s["additionalProperties"]; exists {
		if _, valid := value.(bool); !valid {
			return fmt.Errorf("additionalProperties must be boolean")
		}
	}
	if value, exists := s["enum"]; exists && len(list(value)) == 0 {
		return fmt.Errorf("enum must be a nonempty array")
	}
	if value, exists := s["required"]; exists {
		values := list(value)
		if values == nil {
			return fmt.Errorf("required must be an array")
		}
		seen := map[string]bool{}
		for _, item := range values {
			name, valid := item.(string)
			props, _ := s["properties"].(map[string]any)
			if !valid || name == "" || seen[name] || props[name] == nil {
				return fmt.Errorf("invalid required property")
			}
			seen[name] = true
		}
	}
	if p, ok := s["properties"]; ok {
		props, ok := p.(map[string]any)
		if !ok {
			return fmt.Errorf("properties must be an object")
		}
		for _, value := range props {
			child, ok := value.(map[string]any)
			if !ok {
				return fmt.Errorf("property schema must be an object")
			}
			if err := schemaAt(child, depth+1); err != nil {
				return err
			}
		}
	}
	if child, ok := s["items"]; ok {
		m, ok := child.(map[string]any)
		if !ok {
			return fmt.Errorf("items must be a schema")
		}
		return schemaAt(m, depth+1)
	}
	return nil
}

func Validate(value any, schema map[string]any) error {
	if err := ValidateSchema(schema); err != nil {
		return err
	}
	return validateAt(value, schema, "arguments", 0)
}
func list(value any) []any {
	switch v := value.(type) {
	case []any:
		return v
	case []string:
		r := make([]any, len(v))
		for i, s := range v {
			r[i] = s
		}
		return r
	}
	return nil
}
func numeric(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, !math.IsNaN(n) && !math.IsInf(n, 0)
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}
func validateAt(v any, s map[string]any, path string, depth int) error {
	if depth > 20 {
		return fmt.Errorf("%s exceeds nesting limit", path)
	}
	bad := func() error { return fmt.Errorf("%s violates its parameter schema", path) }
	if e, ok := s["enum"]; ok {
		matched := false
		for _, item := range list(e) {
			a, aOK := numeric(v)
			b, bOK := numeric(item)
			if reflect.DeepEqual(v, item) || (aOK && bOK && a == b) {
				matched = true
			}
		}
		if !matched {
			return bad()
		}
	}
	kind, _ := s["type"].(string)
	switch kind {
	case "object":
		object, ok := v.(map[string]any)
		if !ok {
			return bad()
		}
		props, _ := s["properties"].(map[string]any)
		for _, field := range list(s["required"]) {
			name, ok := field.(string)
			if !ok {
				return bad()
			}
			if _, ok := object[name]; !ok {
				return fmt.Errorf("%s.%s is required", path, name)
			}
		}
		for key, value := range object {
			child, exists := props[key]
			if !exists {
				if allow, ok := s["additionalProperties"].(bool); ok && !allow {
					return fmt.Errorf("%s contains unknown field %s", path, key)
				}
				continue
			}
			if err := validateAt(value, child.(map[string]any), path+"."+key, depth+1); err != nil {
				return err
			}
		}
	case "array":
		values, ok := v.([]any)
		if !ok {
			return bad()
		}
		if !bounds(float64(len(values)), s, "minItems", "maxItems") {
			return bad()
		}
		child, ok := s["items"].(map[string]any)
		if ok {
			for _, value := range values {
				if err := validateAt(value, child, path+"[]", depth+1); err != nil {
					return err
				}
			}
		}
	case "string":
		str, ok := v.(string)
		if !ok || !bounds(float64(utf8.RuneCountInString(str)), s, "minLength", "maxLength") {
			return bad()
		}
	case "number", "integer":
		n, ok := numeric(v)
		if !ok || (kind == "integer" && math.Trunc(n) != n) || !bounds(n, s, "minimum", "maximum") {
			return bad()
		}
	case "boolean":
		if _, ok := v.(bool); !ok {
			return bad()
		}
	case "null":
		if v != nil {
			return bad()
		}
	}
	return nil
}
func bounds(value float64, s map[string]any, min, max string) bool {
	if n, ok := numeric(s[min]); ok && value < n {
		return false
	}
	if n, ok := numeric(s[max]); ok && value > n {
		return false
	}
	return true
}
