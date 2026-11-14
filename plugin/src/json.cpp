#include "json.h"

#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace lc::json
{
	// ---- reading values ------------------------------------------------------

	const Value* Value::Find(const std::string& key) const
	{
		if (m_type != Type::Object)
			return nullptr;
		for (const auto& kv : m_object)
			if (kv.first == key)
				return &kv.second;
		return nullptr;
	}

	std::string Value::GetString(const std::string& key, const std::string& fallback) const
	{
		const Value* v = Find(key);
		return (v && v->m_type == Type::String) ? v->m_string : fallback;
	}

	double Value::GetNumber(const std::string& key, double fallback) const
	{
		const Value* v = Find(key);
		return (v && v->m_type == Type::Number) ? v->m_number : fallback;
	}

	bool Value::GetBool(const std::string& key, bool fallback) const
	{
		const Value* v = Find(key);
		return (v && v->m_type == Type::Bool) ? v->m_bool : fallback;
	}

	Value& Value::Set(const std::string& key, Value v)
	{
		if (m_type != Type::Object)
		{
			*this = MakeObject();
		}
		for (auto& kv : m_object)
		{
			if (kv.first == key)
			{
				kv.second = std::move(v);
				return *this;
			}
		}
		m_object.emplace_back(key, std::move(v));
		return *this;
	}

	Value& Value::Push(Value v)
	{
		if (m_type != Type::Array)
			*this = MakeArray();
		m_array.push_back(std::move(v));
		return *this;
	}

	// ---- writing ---------------------------------------------------------------

	static void DumpString(const std::string& s, std::string& out)
	{
		out += '"';
		for (unsigned char c : s)
		{
			switch (c)
			{
			case '"':  out += "\\\""; break;
			case '\\': out += "\\\\"; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default:
				if (c < 0x20)
				{
					char buf[8];
					sprintf_s(buf, "\\u%04x", c);
					out += buf;
				}
				else
				{
					out += static_cast<char>(c);   // UTF-8 bytes pass through
				}
			}
		}
		out += '"';
	}

	void Value::DumpTo(std::string& out) const
	{
		switch (m_type)
		{
		case Type::Null: out += "null"; break;
		case Type::Bool: out += m_bool ? "true" : "false"; break;
		case Type::Number:
		{
			if (!std::isfinite(m_number)) { out += "null"; break; }
			char buf[32];
			if (m_number == std::floor(m_number) && std::fabs(m_number) < 1e15)
				sprintf_s(buf, "%.0f", m_number);
			else
				sprintf_s(buf, "%.10g", m_number);
			out += buf;
			break;
		}
		case Type::String: DumpString(m_string, out); break;
		case Type::Array:
			out += '[';
			for (size_t i = 0; i < m_array.size(); ++i)
			{
				if (i) out += ',';
				m_array[i].DumpTo(out);
			}
			out += ']';
			break;
		case Type::Object:
			out += '{';
			for (size_t i = 0; i < m_object.size(); ++i)
			{
				if (i) out += ',';
				DumpString(m_object[i].first, out);
				out += ':';
				m_object[i].second.DumpTo(out);
			}
			out += '}';
			break;
		}
	}

	std::string Value::Dump() const
	{
		std::string out;
		DumpTo(out);
		return out;
	}

	// ---- parsing ---------------------------------------------------------------

	namespace
	{
		const int kMaxDepth = 32;

		struct Parser
		{
			const std::string& s;
			size_t i = 0;
			std::string error;

			explicit Parser(const std::string& text) : s(text) {}

			bool Fail(const char* what)
			{
				if (error.empty())
				{
					char buf[96];
					sprintf_s(buf, "%s at position %u", what, static_cast<unsigned>(i));
					error = buf;
				}
				return false;
			}

			void SkipSpace()
			{
				while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r'))
					++i;
			}

			bool Literal(const char* word)
			{
				size_t n = strlen(word);
				if (s.compare(i, n, word) != 0)
					return Fail("unexpected text");
				i += n;
				return true;
			}

			static void AppendUtf8(std::string& out, unsigned cp)
			{
				if (cp < 0x80) out += static_cast<char>(cp);
				else if (cp < 0x800)
				{
					out += static_cast<char>(0xC0 | (cp >> 6));
					out += static_cast<char>(0x80 | (cp & 0x3F));
				}
				else if (cp < 0x10000)
				{
					out += static_cast<char>(0xE0 | (cp >> 12));
					out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
					out += static_cast<char>(0x80 | (cp & 0x3F));
				}
				else
				{
					out += static_cast<char>(0xF0 | (cp >> 18));
					out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
					out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
					out += static_cast<char>(0x80 | (cp & 0x3F));
				}
			}

			bool Hex4(unsigned& out)
			{
				if (i + 4 > s.size())
					return Fail("short \\u escape");
				out = 0;
				for (int k = 0; k < 4; ++k)
				{
					char c = s[i++];
					out <<= 4;
					if (c >= '0' && c <= '9') out |= c - '0';
					else if (c >= 'a' && c <= 'f') out |= c - 'a' + 10;
					else if (c >= 'A' && c <= 'F') out |= c - 'A' + 10;
					else return Fail("bad \\u escape");
				}
				return true;
			}

			bool String(std::string& out)
			{
				++i;   // opening quote
				while (i < s.size())
				{
					char c = s[i++];
					if (c == '"')
						return true;
					if (static_cast<unsigned char>(c) < 0x20)
						return Fail("control character in string");
					if (c != '\\')
					{
						out += c;
						continue;
					}
					if (i >= s.size())
						return Fail("unfinished escape");
					char e = s[i++];
					switch (e)
					{
					case '"': out += '"'; break;
					case '\\': out += '\\'; break;
					case '/': out += '/'; break;
					case 'b': out += '\b'; break;
					case 'f': out += '\f'; break;
					case 'n': out += '\n'; break;
					case 'r': out += '\r'; break;
					case 't': out += '\t'; break;
					case 'u':
					{
						unsigned cp;
						if (!Hex4(cp)) return false;
						if (cp >= 0xD800 && cp <= 0xDBFF)    // surrogate pair
						{
							unsigned lo;
							if (i + 1 < s.size() && s[i] == '\\' && s[i + 1] == 'u')
							{
								i += 2;
								if (!Hex4(lo)) return false;
								if (lo >= 0xDC00 && lo <= 0xDFFF)
									cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
								else
									cp = 0xFFFD;
							}
							else cp = 0xFFFD;
						}
						AppendUtf8(out, cp);
						break;
					}
					default:
						return Fail("unknown escape");
					}
				}
				return Fail("unterminated string");
			}

			bool Number(Value& out)
			{
				size_t start = i;
				if (i < s.size() && s[i] == '-') ++i;
				if (i >= s.size() || !isdigit(static_cast<unsigned char>(s[i])))
					return Fail("bad number");
				while (i < s.size() && (isdigit(static_cast<unsigned char>(s[i])) || s[i] == '.' ||
				       s[i] == 'e' || s[i] == 'E' || s[i] == '+' || s[i] == '-'))
					++i;
				std::string num = s.substr(start, i - start);
				char* end = nullptr;
				double d = strtod(num.c_str(), &end);
				if (!end || *end != '\0')
					return Fail("bad number");
				out = Value(d);
				return true;
			}

			bool Parse(Value& out, int depth)
			{
				if (depth > kMaxDepth)
					return Fail("nested too deeply");
				SkipSpace();
				if (i >= s.size())
					return Fail("unexpected end");
				char c = s[i];
				if (c == '{')
				{
					++i;
					out = Value::MakeObject();
					SkipSpace();
					if (i < s.size() && s[i] == '}') { ++i; return true; }
					while (true)
					{
						SkipSpace();
						if (i >= s.size() || s[i] != '"')
							return Fail("expected a key");
						std::string key;
						if (!String(key)) return false;
						SkipSpace();
						if (i >= s.size() || s[i] != ':')
							return Fail("expected ':'");
						++i;
						Value v;
						if (!Parse(v, depth + 1)) return false;
						out.Set(key, std::move(v));
						SkipSpace();
						if (i < s.size() && s[i] == ',') { ++i; continue; }
						if (i < s.size() && s[i] == '}') { ++i; return true; }
						return Fail("expected ',' or '}'");
					}
				}
				if (c == '[')
				{
					++i;
					out = Value::MakeArray();
					SkipSpace();
					if (i < s.size() && s[i] == ']') { ++i; return true; }
					while (true)
					{
						Value v;
						if (!Parse(v, depth + 1)) return false;
						out.Push(std::move(v));
						SkipSpace();
						if (i < s.size() && s[i] == ',') { ++i; continue; }
						if (i < s.size() && s[i] == ']') { ++i; return true; }
						return Fail("expected ',' or ']'");
					}
				}
				if (c == '"')
				{
					std::string str;
					if (!String(str)) return false;
					out = Value(std::move(str));
					return true;
				}
				if (c == 't') { if (!Literal("true")) return false; out = Value(true); return true; }
				if (c == 'f') { if (!Literal("false")) return false; out = Value(false); return true; }
				if (c == 'n') { if (!Literal("null")) return false; out = Value(); return true; }
				return Number(out);
			}
		};
	}

	bool Parse(const std::string& text, Value& out, std::string& error)
	{
		Parser p(text);
		Value v;
		if (!p.Parse(v, 0))
		{
			error = p.error;
			return false;
		}
		p.SkipSpace();
		if (p.i != text.size())
		{
			p.Fail("extra text after the value");
			error = p.error;
			return false;
		}
		out = std::move(v);
		return true;
	}
}
