using System.Text;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Builds SSE events (WHATWG HTML §9.2.6): text containing line breaks goes out
/// as one <c>data:</c> line per line, closed by a single blank line. A bare
/// <c>\r</c> is a line terminator to a spec-compliant reader, so every break
/// style (<c>\r\n</c>, <c>\r</c>, <c>\n</c>) is split on, mirroring the Python
/// stack's <c>shared/sse.py::sse_frame</c>.
/// </summary>
public static class SseFraming
{
    private static readonly Regex LineBreak = new(@"\r\n|\r|\n", RegexOptions.Compiled);

    public static string Frame(string data, string? eventName = null)
    {
        var frame = new StringBuilder();
        if (eventName is not null)
        {
            frame.Append("event: ").Append(eventName).Append('\n');
        }
        foreach (var line in LineBreak.Split(data))
        {
            frame.Append("data: ").Append(line).Append('\n');
        }
        return frame.Append('\n').ToString();
    }
}
