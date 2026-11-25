import bisq.cli.GrpcClient;
import bisq.core.util.coin.CoinUtil;
import bisq.proto.grpc.OfferInfo;
import bisq.proto.grpc.TxFeeRateInfo;
import org.bitcoinj.core.Coin;

public final class BisqOfferDetails {
    public static void main(String[] args) {
        if (args.length != 5) {
            System.err.println("uso: host port password direction currency");
            System.exit(2);
        }
        GrpcClient client = new GrpcClient(args[0], Integer.parseInt(args[1]), args[2]);
        long feeRate = -1;
        try {
            TxFeeRateInfo feeInfo = client.getTxFeeRate();
            feeRate = feeInfo.getUseCustomTxFeeRate()
                    ? feeInfo.getCustomTxFeeRate() : feeInfo.getFeeServiceRate();
        } catch (RuntimeException ignored) {
            // The API rate-limits this method.  The monitor reuses its last valid rate.
        }
        for (OfferInfo offer : client.getOffers(args[3], args[4])) {
            long minTakerFee = CoinUtil.getTakerFee(true, Coin.valueOf(offer.getMinAmount())).value;
            long maxTakerFee = CoinUtil.getTakerFee(true, Coin.valueOf(offer.getAmount())).value;
            System.out.printf("%s\t%s\t%s\t%d\t%d\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%b\t%d\t%d\t%d\t%d%n",
                    offer.getId(), offer.getDirection(), offer.getPrice(),
                    offer.getMinAmount(), offer.getAmount(), offer.getMinVolume(),
                    offer.getVolume(), offer.getPaymentMethodShortName(),
                    offer.getBuyerSecurityDeposit(), offer.getSellerSecurityDeposit(),
                    offer.getTxFee(), offer.getMakerFee(), offer.getIsCurrencyForMakerFeeBtc(),
                    offer.getDate(), minTakerFee, maxTakerFee, feeRate);
        }
    }
}
