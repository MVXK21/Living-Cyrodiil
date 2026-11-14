#pragma once

// Makes an NPC "speak" a reply through the game's own dialogue system
// (Phase 2 step 6, docs/phase2-plan.md).
//
// The NPC says a placeholder line from LivingCyrodiil.esp (topic LCReply)
// with SayTo, which gives a positional voice, lip movement and a real
// subtitle. A reply comes as 1-4 chunks, said as parts one after another:
// the .esp has one single-response line per part, picked by the LCPart
// global, and Tick() starts the next part when the current subtitle ends.
// (Outside the dialogue window the game says only a line's first response,
// so one line with several responses doesn't work.) Oblivion reads a line's
// text from the .esp file when it's said, so the text can't be changed in
// memory beforehand. Instead, while a part plays, Tick() finds its
// placeholder ("(Living Cyrodiil placeholder <p>)") in the subtitle menu and
// replaces it with chunk p.
//
// Main thread only.

#include <string>
#include <vector>

namespace lc::speak
{
	// Must match tools/build_esp.py.
	constexpr const char* kPluginFile = "LivingCyrodiil.esp";
	constexpr const char* kQuestEditorId = "LCQuest";
	constexpr const char* kTopicEditorId = "LCReply";
	constexpr const char* kPartGlobal = "LCPart";
	constexpr unsigned    kPartGlobalLocalId = 0x000803;   // form id within the .esp (mod index added at run time)
	constexpr const char* kPlaceholderPrefix = "(Living Cyrodiil placeholder ";   // + "<p>)"
	constexpr int kMaxChunks = 4;
	// Voice file id of the line for each reply part (index 0 = part 1): the
	// INFO's form id with the mod index cleared.
	constexpr const char* kLineFileIds[kMaxChunks] = { "00000802", "00000804", "00000805", "00000806" };

	void Init(void* consoleInterface);    // OBSEConsoleInterface*

	// Make the NPC with this reference id (hex) say a reply to the player, as
	// `chunks` (one subtitle each; more than 4 are merged into the last).
	// Before SayTo it writes a silent voice file per chunk for the NPC's race
	// and sex, sized to that chunk's reading time: the game won't say a line
	// that has no voice file, and the audio length sets how long each
	// subtitle stays up. If no subtitle shows within a few seconds, the text
	// is shown as a notification instead. Returns false (and logs why) if the
	// NPC can't be found; the caller then shows the text itself.
	//
	// `voiced` (Phase 3): the companion sends each part's speech as an MP3
	// (AudioReady). Each part then waits up to 10 s for its audio, copies it
	// over the part's voice file and stays up as long as the speech; a part
	// whose audio doesn't come is said silently. `turn` ties the audio to
	// this reply.
	//
	// `filler` (step 4): a short sound ("Hmm.") the companion sends when the
	// reply is slow. A real reply for the same NPC that arrives while it plays
	// waits, with its audio, and starts as soon as the filler ends.
	//
	// `streaming` (step 4b): `utf8Chunks` is part 1 only, sent as soon as the
	// companion has it. The whole list of parts follows (PartsReady); when
	// part 1 is over and the list hasn't come, the NPC waits up to 10 s.
	bool Say(const std::string& refIdHex, const std::vector<std::string>& utf8Chunks,
		bool voiced = false, int turn = 0, bool filler = false, bool streaming = false);

	// Every part of a streamed reply (reply_parts), part 1 first. Ignored
	// unless it matches the streamed reply showing now or waiting behind a
	// filler (same NPC and turn).
	void PartsReady(const std::string& refIdHex, int turn, const std::vector<std::string>& utf8Chunks);

	// A part's speech is ready (reply_audio): `utf8Path` is the MP3 and
	// `seconds` its length, or `error` says why there's none. Ignored unless
	// it matches the voiced reply showing now (same NPC and turn).
	// `utf8LipPath` (Phase 4, may be empty): the part's .lip, copied next to
	// the voice file so the game moves the NPC's mouth with the speech.
	void AudioReady(const std::string& refIdHex, int turn, int part, const std::string& utf8Path,
		double seconds, const std::string& error, const std::string& utf8LipPath = {});

	// Call every frame: swaps the placeholder subtitles for the reply text.
	void Tick();

	// Stops watching the current reply and restores the player's subtitle
	// setting. Call on save load, main menu and game exit.
	void Stop();
}
