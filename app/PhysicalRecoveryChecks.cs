using System.IO;
using System.Text.Json;

namespace OxpInstaller;

internal static class PhysicalRecoveryChecks
{
    internal static async Task<string> RunAsync()
    {
        var events=new List<string>();
        void Refuse(Action action,string name) { try{action();}catch(IOException){events.Add(name);return;}throw new IOException("Accepted: "+name); }
        const long mib=1048576,gib=1073741824;
        var snapshot=PartitionInventory.ExampleJson;var gap=DiskPlanning.FreeSpaces(snapshot).Single();
        var selection=new PartitionSelection(snapshot,false,gap.Disk,gap.Start,64*gib);
        var parts=PreparedDisk.Roles.Select((name,i)=>new PreparedPartition(i+1,name,Guid.Parse("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"),Guid.NewGuid(),mib+i*8*gib,8*gib,0)).ToArray();
        var image=new PreparedDisk(Guid.NewGuid(),80*gib,parts);
        var plan=PhysicalInstallation.Plan(selection,InstallationTargetKind.InternalShared,image);
        var source=new MediaSource("fixture.img",image.Size,1,1,1,image.Id);
        var native=new NativeDiskInfo(gap.Disk,gap.DiskSize,512,17,"","","",false);
        var state=new PhysicalRecoveryState(1,"incomplete",native,plan,source,PreparedDisk.Roles[..7],"interrupted");
        PhysicalInstallation.ValidateRecovery(state,source,plan,image);events.Add("valid-seven-role-recovery");
        var defaultPlan = plan with { MakeDefaultBoot = true };
        PhysicalInstallation.ValidateRecovery(state with { Plan = defaultPlan }, source, defaultPlan, image); events.Add("default-boot-choice-preserved-in-recovery");
        Refuse(() => PhysicalInstallation.ValidateRecovery(state, source, defaultPlan, image), "changed-default-boot-choice-refused");
        var restart=PhysicalInstallation.RecoveryPrefix(state.Completed,new HashSet<string>{"efi-A","efi-B"});
        if(!restart.SequenceEqual(new[]{"esp"}))throw new IOException("Wrong journal rollback prefix.");
        var verified=state.Completed.Except(new[]{"efi-A","efi-B"}).ToHashSet();
        if(!PreparedDisk.Roles.Skip(restart.Length).Where(r=>!verified.Contains(r)).SequenceEqual(new[]{"efi-A","efi-B","home"}))throw new IOException("Recovery would rewrite verified root filesystems.");
        events.Add("restore-only-new-fat-slots-and-incomplete-home");
        Refuse(()=>PhysicalInstallation.RecoveryPrefix(state.Completed,new HashSet<string>{"rootfs-A"}),"non-fat-mismatch-cannot-be-ignored");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state with{Completed=["home"]},source,plan,image),"non-prefix-refused");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state with{Completed=["esp","esp"]},source,plan,image),"duplicate-completed-refused");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state with{Phase="reviewed"},source,plan,image),"unappended-journal-refused");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state,source with{FileId=2},plan,image),"source-replacement-refused");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state,source,plan with{Added=plan.Added.Select((p,i)=>i==7?p with{Offset=p.Offset+mib}:p).ToArray()},image),"altered-destination-refused");
        Refuse(()=>PhysicalInstallation.ValidateRecovery(state with{Device=native with{Size=native.Size-mib}},source,plan,image),"changed-device-size-refused");
        var appended=new NativeLayout(1,plan.DiskId,plan.Preserved.Concat(plan.Added.Select(p=>p.Native)).OrderBy(p=>p.Offset).ToArray());
        PhysicalInstallation.RequireLayout(plan,appended,true);events.Add("exact-appended-layout-accepted");
        Refuse(()=>PhysicalInstallation.RequireLayout(plan,appended with{Partitions=appended.Partitions.Skip(1).ToArray()},true),"missing-existing-partition-refused");
        Refuse(()=>PhysicalInstallation.RequireLayout(plan,appended with{Partitions=appended.Partitions.Where(p=>p.Id!=plan.Added[7].Id).ToArray()},true),"missing-home-refused");
        var directory=Path.Combine(InstallationController.Work,"physical-recovery-check-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
        CheckFirmwareRetry(directory, events);
        using var a=new FileStream(Path.Combine(directory,"source.bin"),FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None);
        using var b=new FileStream(Path.Combine(directory,"target.bin"),FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None);
        a.SetLength(8*mib);b.SetLength(9*mib);
        a.Position=6*mib; a.WriteByte(87);b.Position=7*mib;b.WriteByte(87);a.Flush();b.Flush();
        await PhysicalInstallation.CompareCompletedAsync(a,b,0,mib,8*mib,CancellationToken.None);events.Add("full-comparison-crosses-buffer-boundary");
        b.Position=8*mib+512;b.WriteByte(91);b.Flush();
        var refused=false;try{await PhysicalInstallation.CompareCompletedAsync(a,b,0,mib,8*mib,CancellationToken.None);}catch(CompletedRangeMismatchException error){refused=error.Message.Contains((7*mib+512).ToString());}
        if(!refused)throw new IOException("Late completed-range mismatch accepted.");events.Add("late-prefix-mismatch-refused-before-copy");
        b.Position=8*mib+512;if(b.ReadByte()!=91)throw new IOException("Comparison modified target.");events.Add("prefix-verification-is-read-only");
        using var cancel=new CancellationTokenSource();cancel.Cancel();refused=false;
        try{await PhysicalInstallation.CompareCompletedAsync(a,b,0,mib,8*mib,cancel.Token);}catch(OperationCanceledException){refused=true;}
        if(!refused)throw new IOException("Cancellation ignored.");events.Add("comparison-cancellation");
        var result=JsonSerializer.Serialize(new{passed=true,events,scope="Pure recovery validation, in-memory firmware rollback/retry and complete ordinary-file comparison; no physical disk or firmware writes."},new JsonSerializerOptions{WriteIndented=true});
        File.WriteAllText(Path.Combine(directory,"result.json"),result);return result;
    }

    private static void CheckFirmwareRetry(string directory, List<string> events)
    {
        var run = Path.Combine(directory, "firmware-retry"); Directory.CreateDirectory(run);
        var esp = new PreparedPartition(17, "esp", new Guid("c12a7328-f81f-11d2-ba4b-00a0c93ec93b"), Guid.NewGuid(), 1048576, 64 * 1048576, 0);
        var store = new Dictionary<string, (byte[] Data, uint Attributes)> { ["BootOrder"] = (new byte[] { 1, 0 }, 7), ["Boot0001"] = (new byte[] { 42 }, 7) };
        (byte[] Data, uint Attributes)? Read(string name) => store.TryGetValue(name, out var value) ? (value.Data.ToArray(), value.Attributes) : null;
        var reject = true;
        void Write(string name, byte[] data, uint attributes)
        {
            if (name == "BootOrder" && reject) throw new IOException("Injected firmware order failure.");
            if (data.Length == 0) store.Remove(name); else store[name] = (data.ToArray(), attributes);
        }
        try { FirmwareBoot.RegisterCore(esp, run, false, Read, Write); throw new IOException("Expected firmware failure."); }
        catch (IOException error) when (error.Message == "Injected firmware order failure.") { }
        if (!store["BootOrder"].Data.SequenceEqual(new byte[] { 1, 0 }) || store.ContainsKey("Boot6000"))
            throw new IOException("Firmware failure did not roll back.");
        events.Add("firmware-failed-attempt-rolled-back-in-memory");
        var originalReceipt = File.ReadAllBytes(Path.Combine(run, "firmware-before.json"));
        foreach (var fault in new[] { "outside-order", "outside-attributes", "leftover-entry", "changed-esp", "changed-choice", "malformed", "oversized", "outside-entry-range", "success-result" })
        {
            var refusedRun = Path.Combine(directory, "firmware-refuse-" + fault); Directory.CreateDirectory(refusedRun);
            var receipt = Path.Combine(refusedRun, "firmware-before.json");
            File.WriteAllBytes(receipt, originalReceipt);
            var currentOrder = store["BootOrder"];
            if (fault == "outside-order") store["BootOrder"] = (new byte[] { 7, 0 }, 7);
            if (fault == "outside-attributes") store["BootOrder"] = (new byte[] { 1, 0 }, 3);
            if (fault == "leftover-entry") store["Boot6000"] = (FirmwareBoot.LoadOption(esp), 7);
            if (fault == "malformed") File.WriteAllText(receipt, "{}");
            if (fault == "oversized") File.WriteAllBytes(receipt, new byte[256 * 1024 + 1]);
            if (fault == "outside-entry-range") File.WriteAllText(receipt, File.ReadAllText(receipt).Replace("Boot6000", "Boot0002", StringComparison.Ordinal));
            if (fault == "success-result") File.WriteAllText(Path.Combine(refusedRun, "firmware-result.json"), "{}");
            var beforeReceipt = File.ReadAllBytes(receipt);
            var writes = 0; var refused = false;
            try { FirmwareBoot.RegisterCore(fault == "changed-esp" ? esp with { Id = Guid.NewGuid() } : esp, refusedRun, fault == "changed-choice", Read, (_, _, _) => writes++); }
            catch (IOException) { refused = true; }
            if (!refused || writes != 0 || !File.ReadAllBytes(receipt).SequenceEqual(beforeReceipt)
                || Directory.GetFiles(refusedRun, "firmware-before.retry-*.json").Length != 0)
                throw new IOException("Unsafe firmware retry: " + fault);
            events.Add("firmware-retry-refuses-" + fault + "-before-archive-or-write");
            store["BootOrder"] = currentOrder; store.Remove("Boot6000");
        }
        reject = false;
        FirmwareBoot.RegisterCore(esp, run, false, Read, Write);
        if (!File.Exists(Path.Combine(run, "firmware-result.json")) || store.Count != 3 || !store["Boot0001"].Data.SequenceEqual(new byte[] { 42 }))
            throw new IOException("Firmware retry did not register exactly one entry.");
        events.Add("firmware-retry-after-rollback-uses-same-deployment-directory");
        var archives = Directory.GetFiles(run, "firmware-before.retry-*.json");
        if (archives.Length != 1 || !File.ReadAllBytes(archives[0]).SequenceEqual(originalReceipt))
            throw new IOException("Previous firmware receipt evidence was lost during retry.");
        events.Add("firmware-retry-preserves-original-receipt-exact-bytes");
    }
}
