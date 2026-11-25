import bisq.cli.GrpcClient;
import bisq.proto.grpc.AddressBalanceInfo;

public final class BisqWalletDetails {
    private static void print(AddressBalanceInfo info) {
        System.out.printf("%s\t%d\t%d\t%b%n", info.getAddress(), info.getBalance(),
                info.getNumConfirmations(), info.getIsAddressUnused());
    }

    public static void main(String[] args) {
        if (args.length < 4 || args.length > 5) {
            System.err.println("uso: host port password funding|address [address]");
            System.exit(2);
        }
        GrpcClient client = new GrpcClient(args[0], Integer.parseInt(args[1]), args[2]);
        if ("address".equals(args[3]) && args.length == 5) {
            print(client.getAddressBalance(args[4]));
            return;
        }
        if ("funding".equals(args[3]) && args.length == 4) {
            AddressBalanceInfo fallback = null;
            for (AddressBalanceInfo info : client.getFundingAddresses(false)) {
                if (fallback == null) fallback = info;
                if (info.getIsAddressUnused()) {
                    print(info);
                    return;
                }
            }
            if (fallback != null) {
                print(fallback);
                return;
            }
            throw new IllegalStateException("nenhum endereço BTC disponível");
        }
        throw new IllegalArgumentException("comando inválido");
    }
}
