#pragma once

// Minimal JSON for the companion protocol (docs/protocol.md): parse one
// message line, build messages, write them out. Handles the full JSON syntax
// (objects, arrays, strings with escapes and \u, numbers, true/false/null).
// Strings are UTF-8 throughout.

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace lc::json
{
	class Value
	{
	public:
		enum class Type { Null, Bool, Number, String, Array, Object };

		Value() = default;
		Value(std::nullptr_t) {}
		Value(bool b) : m_type(Type::Bool), m_bool(b) {}
		Value(int n) : m_type(Type::Number), m_number(n) {}
		Value(unsigned n) : m_type(Type::Number), m_number(n) {}
		Value(double n) : m_type(Type::Number), m_number(n) {}
		Value(const char* s) : m_type(Type::String), m_string(s ? s : "") {}
		Value(std::string s) : m_type(Type::String), m_string(std::move(s)) {}

		static Value MakeArray() { Value v; v.m_type = Type::Array; return v; }
		static Value MakeObject() { Value v; v.m_type = Type::Object; return v; }

		Type type() const { return m_type; }
		bool IsObject() const { return m_type == Type::Object; }
		bool IsString() const { return m_type == Type::String; }
		bool IsNumber() const { return m_type == Type::Number; }

		// Readers with fallbacks: never throw, so a malformed message can't crash the game.
		std::string GetString(const std::string& key, const std::string& fallback = "") const;
		double      GetNumber(const std::string& key, double fallback = 0) const;
		bool        GetBool(const std::string& key, bool fallback = false) const;
		const Value* Find(const std::string& key) const;

		const std::string& AsString() const { return m_string; }
		double AsNumber() const { return m_number; }
		bool AsBool() const { return m_bool; }
		const std::vector<Value>& AsArray() const { return m_array; }

		// Builders
		Value& Set(const std::string& key, Value v);   // makes this an object if it isn't
		Value& Push(Value v);                          // makes this an array if it isn't

		std::string Dump() const;                      // compact, one line

	private:
		void DumpTo(std::string& out) const;

		Type m_type = Type::Null;
		bool m_bool = false;
		double m_number = 0;
		std::string m_string;
		std::vector<Value> m_array;
		std::vector<std::pair<std::string, Value>> m_object;   // keeps insertion order
	};

	// Parse one JSON document. Returns false (and fills error) on bad input.
	bool Parse(const std::string& text, Value& out, std::string& error);
}
