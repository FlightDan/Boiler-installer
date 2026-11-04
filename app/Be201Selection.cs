namespace OxpInstaller;

// Presentation state only. A caller must independently verify target-bound hardware
// and enforce the existing installation trust checks before using a selection.
internal sealed class Be201Selection
{
    internal enum TargetMatch { Unknown, Candidate, Different, Confirmed }
    internal enum Provider { None, Model, Common }
    internal sealed record TargetKey(Guid DiskId, string Bus, string Model, string Serial, long DiskSize)
    {
        internal static TargetKey FromReviewedSelection(PartitionSelection selection)
        {
            var key = SsdBootRepair.PreferenceTarget(selection);
            return new(key.DiskId, key.Bus, key.Model, key.Serial, key.DiskSize);
        }
    }
    internal sealed record State(Provider Source, bool Enabled, bool Selected);
    private readonly Dictionary<TargetKey, bool> choices = new();

    internal State Get(TargetKey? target, TargetMatch match, bool modelAvailable, bool commonAvailable)
    {
        var source = modelAvailable ? Provider.Model : commonAvailable ? Provider.Common : Provider.None;
        var enabled = target is not null && match == TargetMatch.Confirmed && source != Provider.None;
        return new(source, enabled, enabled && (!choices.TryGetValue(target!, out var selected) || selected));
    }

    internal void Remember(TargetKey? target, TargetMatch match, bool modelAvailable, bool commonAvailable, bool selected)
    {
        if (Get(target, match, modelAvailable, commonAvailable).Enabled)
            choices[target!] = selected;
    }
}
