import bisq.cli.GrpcClient;
import bisq.proto.grpc.OfferInfo;

public final class BisqOfferDetails {
    public static void main(String[] args) {
        if (args.length != 5) {
            System.err.println("uso: host port password direction currency");
            System.exit(2);
        }
        GrpcClient client = new GrpcClient(args[0], Integer.parseInt(args[1]), args[2]);
        for (OfferInfo offer : client.getOffers(args[3], args[4])) {
            System.out.printf("%s\t%s\t%s\t%d\t%d\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%b\t%d%n",
                    offer.getId(), offer.getDirection(), offer.getPrice(),
                    offer.getMinAmount(), offer.getAmount(), offer.getMinVolume(),
                    offer.getVolume(), offer.getPaymentMethodShortName(),
                    offer.getBuyerSecurityDeposit(), offer.getSellerSecurityDeposit(),
                    offer.getTxFee(), offer.getMakerFee(), offer.getIsCurrencyForMakerFeeBtc(),
                    offer.getDate());
        }
    }
}
