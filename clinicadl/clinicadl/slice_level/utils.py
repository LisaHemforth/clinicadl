import torch
from torch.utils.data import Dataset
import os, shutil
from os import path
import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedShuffleSplit
from time import time
import tempfile

__author__ = "Junhao Wen"
__copyright__ = "Copyright 2018 The Aramis Lab Team"
__credits__ = ["Junhao Wen"]
__license__ = "See LICENSE.txt file"
__version__ = "0.1.0"
__maintainer__ = "Junhao Wen"
__email__ = "junhao.wen89@gmail.com"
__status__ = "Development"


#################################
# CNN train / test
#################################

def train(model, data_loader, options, loss_func, optimizer, writer, epoch, iteration, model_mode="train", global_step=0):
    """
    This is the function to train, validate or test the model, depending on the model_mode parameter.
    :param model:
    :param data_loader:
    :param options.:
    :param loss_func:
    :param optimizer:
    :param writer:
    :param epoch:
    :return:
    """
    # main training loop
    acc = 0.0
    loss = 0.0

    subjects = []
    y_ground = []
    y_hat = []
    proba = []
    print("Start %s!" % model_mode)
    if model_mode == "train":
        model.train() ## set the model to training mode
        print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
        tend = time()
        total_time = 0

        for i, batch_data in enumerate(data_loader):
            t0 = time()
            total_time = total_time + t0 - tend

            if options.use_gpu:
                imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
            else:
                imgs, labels = batch_data['image'], batch_data['label']

            ## add the participant_id + session_id
            image_ids = batch_data['image_id']
            subjects.extend(image_ids)

            gound_truth_list = labels.data.cpu().numpy().tolist()
            y_ground.extend(gound_truth_list)

            print('The group true label is %s' % (str(labels)))
            output = model(imgs)

            _, predict = output.topk(1)
            predict_list = predict.data.cpu().numpy().tolist()
            predict_list = [item for sublist in predict_list for item in sublist]
            y_hat.extend(predict_list)

            print("output.device: " + str(output.device))
            print("labels.device: " + str(labels.device))
            print("The predicted label is: " + str(output))
            loss_batch = loss_func(output, labels)

            ## adding the probability
            proba.extend(output.data.cpu().numpy().tolist())

            ## calculate the balanced accuracy
            results = evaluate_prediction(gound_truth_list, predict_list)
            accuracy = results['balanced_accuracy']
            acc += accuracy
            loss += loss_batch.item()

            print("For batch %d, training loss is : %f" % (i, loss_batch.item()))
            print("For batch %d, training accuracy is : %f" % (i, accuracy))

            writer.add_scalar('classification accuracy', accuracy, global_step)
            writer.add_scalar('loss', loss_batch, global_step)

            # Unlike tensorflow, in Pytorch, we need to manully zero the graident before each backpropagation step, becase Pytorch accumulates the gradients
            # on subsequent backward passes. The initial designing for this is convenient for training RNNs.
            optimizer.zero_grad()
            loss_batch.backward()
            optimizer.step()

            ## Update global_step only during training
            global_step = i + epoch * len(data_loader)

            # delete the temporal varibles taking the GPU memory
            # del imgs, labels
            del imgs, labels, output, predict, gound_truth_list, loss_batch, accuracy, results
            # Releases all unoccupied cached memory
            torch.cuda.empty_cache()
            tend = time()
        print('Mean time per batch (train):', total_time / len(data_loader))

        accuracy_batch_mean = acc / len(data_loader)
        loss_batch_mean = loss / len(data_loader)
        torch.cuda.empty_cache()

    elif model_mode == "valid":
        model.eval() ## set the model to evaluation mode
        torch.cuda.empty_cache()
        with torch.no_grad():
            ## torch.no_grad() needs to be set, otherwise the accumulation of gradients would explose the GPU memory.
            print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
            for i, batch_data in enumerate(data_loader):
                if options.use_gpu:
                    imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
                else:
                    imgs, labels = batch_data['image'], batch_data['label']

                ## add the participant_id + session_id
                image_ids = batch_data['image_id']
                subjects.extend(image_ids)

                gound_truth_list = labels.data.cpu().numpy().tolist()
                y_ground.extend(gound_truth_list)

                print('The group true label is %s' % (str(labels)))
                output = model(imgs)

                _, predict = output.topk(1)
                predict_list = predict.data.cpu().numpy().tolist()
                predict_list = [item for sublist in predict_list for item in sublist]
                y_hat.extend(predict_list)

                print("output.device: " + str(output.device))
                print("labels.device: " + str(labels.device))
                print("The predicted label is: " + str(output))
                loss_batch = loss_func(output, labels)

                ## adding the probability
                proba.extend(output.data.cpu().numpy().tolist())

                ## calculate the balanced accuracy
                results = evaluate_prediction(gound_truth_list, predict_list)
                accuracy = results['balanced_accuracy']
                loss += loss_batch.item()
                print("For batch %d, validation accuracy is : %f" % (i, accuracy))

                # delete the temporal varibles taking the GPU memory
                # del imgs, labels
                del imgs, labels, output, predict, gound_truth_list, accuracy, loss_batch, results
                # Releases all unoccupied cached memory
                torch.cuda.empty_cache()

            results = soft_voting_subject_level(y_ground, y_hat, subjects, proba, iteration)
            accuracy_batch_mean = results['balanced_accuracy']
            loss_batch_mean = loss / len(data_loader)

            writer.add_scalar('classification accuracy', accuracy_batch_mean, epoch)
            writer.add_scalar('loss', loss_batch_mean, epoch)

            torch.cuda.empty_cache()

    return subjects, y_ground, y_hat, proba, accuracy_batch_mean, global_step, loss_batch_mean


def test(model, data_loader, options):
    """
    The function to evaluate the testing data for the trained classifiers
    :param model:
    :param test_loader:
    :param options.:
    :return:
    """

    subjects = []
    y_ground = []
    y_hat = []
    proba = []
    print("Start evaluate the model!")

    model.eval()  ## set the model to evaluation mode
    torch.cuda.empty_cache()
    with torch.no_grad():
        ## torch.no_grad() needs to be set, otherwise the accumulation of gradients would explose the GPU memory.
        print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
        for i, batch_data in enumerate(data_loader):
            if options.use_gpu:
                imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
            else:
                imgs, labels = batch_data['image'], batch_data['label']

            ## add the participant_id + session_id
            image_ids = batch_data['image_id']
            subjects.extend(image_ids)

            gound_truth_list = labels.data.cpu().numpy().tolist()
            y_ground.extend(gound_truth_list)

            print('The group true label is %s' % (str(labels)))
            output = model(imgs)

            _, predict = output.topk(1)
            predict_list = predict.data.cpu().numpy().tolist()
            predict_list = [item for sublist in predict_list for item in sublist]
            y_hat.extend(predict_list)

            print("output.device: " + str(output.device))
            print("labels.device: " + str(labels.device))
            print("The predicted label is: " + str(output))

            ## adding the probability
            proba.extend(output.data.cpu().numpy().tolist())

            ## calculate the balanced accuracy
            results = evaluate_prediction(gound_truth_list, predict_list)
            accuracy = results['balanced_accuracy']
            print("For batch %d, test accuracy is : %f" % (i, accuracy))

            # delete the temporal varibles taking the GPU memory
            del imgs, labels, output, predict, gound_truth_list, accuracy, results
            # Releases all unoccupied cached memory
            torch.cuda.empty_cache()

        ## calculate the balanced accuracy
        results = evaluate_prediction(y_ground, y_hat)
        accuracy_batch_mean = results['balanced_accuracy']
        torch.cuda.empty_cache()

    return subjects, y_ground, y_hat, proba, accuracy_batch_mean


def evaluate_prediction(y, y_hat):

    """
    This is a function to calculate the different metrics based on the list of true label and predicted label
    :param y: list
    :param y_hat: list
    :return:
    """

    true_positive = 0.0
    true_negative = 0.0
    false_positive = 0.0
    false_negative = 0.0

    tp = []
    tn = []
    fp = []
    fn = []

    for i in range(len(y)):
        if y[i] == 1:
            if y_hat[i] == 1:
                true_positive += 1
                tp.append(i)
            else:
                false_negative += 1
                fn.append(i)
        else:  # -1
            if y_hat[i] == 0:
                true_negative += 1
                tn.append(i)
            else:
                false_positive += 1
                fp.append(i)

    accuracy = (true_positive + true_negative) / (true_positive + true_negative + false_positive + false_negative)

    if (true_positive + false_negative) != 0:
        sensitivity = true_positive / (true_positive + false_negative)
    else:
        sensitivity = 0.0

    if (false_positive + true_negative) != 0:
        specificity = true_negative / (false_positive + true_negative)
    else:
        specificity = 0.0

    if (true_positive + false_positive) != 0:
        ppv = true_positive / (true_positive + false_positive)
    else:
        ppv = 0.0

    if (true_negative + false_negative) != 0:
        npv = true_negative / (true_negative + false_negative)
    else:
        npv = 0.0

    balanced_accuracy = (sensitivity + specificity) / 2

    results = {'accuracy': accuracy,
               'balanced_accuracy': balanced_accuracy,
               'sensitivity': sensitivity,
               'specificity': specificity,
               'ppv': ppv,
               'npv': npv,
               'confusion_matrix': {'tp': len(tp), 'tn': len(tn), 'fp': len(fp), 'fn': len(fn)}
               }

    return results

# def save_checkpoint(state, is_best, checkpoint_dir, filename='checkpoint.pth.tar'):
#     """
#     This is the function to save the best model during validation process
#     :param state: the parameters that you wanna save
#     :param is_best: if the performance is better than before
#     :param checkpoint_dir:
#     :param filename:
#     :return:
#         checkpoint.pth.tar: this is the model trained by the last epoch, useful to retrain from this stopping point
#         model_best.pth.tar: if is_best is Ture, this is the best model during the validation, useful for testing the performances of the model
#     """
#     import shutil, os
#     if not os.path.exists(checkpoint_dir):
#         os.makedirs(checkpoint_dir)
#     torch.save(state, os.path.join(checkpoint_dir, filename))
#     if is_best:
#         shutil.copyfile(os.path.join(checkpoint_dir, filename),  os.path.join(checkpoint_dir, 'model_best.pth.tar'))

# def subject_diagnosis_df(subject_session_df):
#     """
#     Creates a DataFrame with only one occurence of each subject and the most early diagnosis
#     Some subjects may not have the baseline diagnosis (ses-M00 doesn't exist)
#
#     :param subject_session_df: (DataFrame) a DataFrame with columns containing 'participant_id', 'session_id', 'diagnosis'
#     :return: DataFrame with the same columns as the input
#     """
#     temp_df = subject_session_df.set_index(['participant_id', 'session_id'])
#     subjects_df = pd.DataFrame(columns=subject_session_df.columns)
#     for subject, subject_df in temp_df.groupby(level=0):
#         session_nb_list = [int(session[5::]) for _, session in subject_df.index.values]
#         session_nb_list.sort()
#         session_baseline_nb = session_nb_list[0]
#         if session_baseline_nb < 10:
#             session_baseline = 'ses-M0' + str(session_baseline_nb)
#         else:
#             session_baseline = 'ses-M' + str(session_baseline_nb)
#         row_baseline = list(subject_df.loc[(subject, session_baseline)])
#         row_baseline.insert(0, subject)
#         row_baseline.insert(1, session_baseline)
#         row_baseline = np.array(row_baseline).reshape(1, len(row_baseline))
#         row_df = pd.DataFrame(row_baseline, columns=subject_session_df.columns)
#         subjects_df = subjects_df.append(row_df)
#
#     subjects_df.reset_index(inplace=True, drop=True)
#     return subjects_df


#################################
# Datasets
#################################

def load_split_by_slices(training_tsv, validation_tsv, mri_plane=0, val_size=0.15):
    """
    This is a function to gather the training and validation tsv together, then do the bad data split by slice.
    :param training_tsv:
    :param validation_tsv:
    :return:
    """

    df_training = pd.read_csv(training_tsv, sep='\t')
    df_validation = pd.read_csv(validation_tsv, sep='\t')
    df_all = pd.concat([df_training, df_validation])
    df_all = df_all.reset_index(drop=True)

    if mri_plane == 0:
        slices_per_patient = 169 - 40
        slice_index = range(20, 169 - 20)
    elif mri_plane == 1:
        slices_per_patient = 208 - 40
        slice_index = range(20, 208 - 20)
    else:
        slices_per_patient = 179 - 40
        slice_index = range(20, 179 - 20)

    participant_list = list(df_all['participant_id'])
    session_list = list(df_all['session_id'])
    label_list = list(df_all['diagnosis'])

    slice_participant_list = [ele for ele in participant_list for _ in range(slices_per_patient)]
    slice_session_list = [ele for ele in session_list for _ in range(slices_per_patient)]
    slice_label_list = [ele for ele in label_list for _ in range(slices_per_patient)]
    slice_index_list = slice_index * len(label_list)

    df_final = pd.DataFrame(columns=['participant_id', 'session_id', 'slice_id', 'diagnosis'])
    df_final['participant_id'] = np.array(slice_participant_list)
    df_final['session_id'] = np.array(slice_session_list)
    df_final['slice_id'] = np.array(slice_index_list)
    df_final['diagnosis'] = np.array(slice_label_list)

    y = np.array(slice_label_list)
    # split the train data into training and validation set
    skf_2 = StratifiedShuffleSplit(n_splits=1, test_size=val_size, random_state=10000)
    indices = next(skf_2.split(np.zeros(len(y)), y))
    train_ind, valid_ind = indices

    df_sub_train = df_final.iloc[train_ind]
    df_sub_valid = df_final.iloc[valid_ind]

    train_tsv_path = os.path.join(tempfile.mkdtemp(), 'bad_data_split_train.tsv')
    valid_tsv_path = os.path.join(tempfile.mkdtemp(), 'bad_data_split_valid.tsv')

    df_sub_train.to_csv(train_tsv_path, sep='\t', index=False)
    df_sub_valid.to_csv(valid_tsv_path, sep='\t', index=False)

    return train_tsv_path, valid_tsv_path


class MRIDataset_slice(Dataset):
    """
    This class reads the CAPS of image processing pipeline of DL

    To note, this class processes the MRI to be RGB for transfer learning.

    Return: a Pytorch Dataset objective
    """

    def __init__(self, caps_directory, tsv, transformations=None, transfer_learning=False, mri_plane=0):
        """
        Args:
            caps_directory (string): the output folder of image processing pipeline.
            tsv (string): the tsv containing three columns, participant_id, session_id and diagnosis.
            transformations (callable, optional): if the data sample should be done some transformations or not, such as resize the image.

        To note, for each view:
            Axial_view = "[:, :, slice_i]"
            Coronal_veiw = "[:, slice_i, :]"
            Saggital_view= "[slice_i, :, :]"

        """
        self.caps_directory = caps_directory
        self.tsv = tsv
        self.transformations = transformations
        self.transfer_learning = transfer_learning
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1}
        self.mri_plane = mri_plane

        df = pd.read_csv(tsv, sep='\t')
        if ('diagnosis' != list(df.columns.values)[2]) and ('session_id' != list(df.columns.values)[1]) and (
            'participant_id' != list(df.columns.values)[0]):
            raise Exception('the data file is not in the correct format.')
        participant_list = list(df['participant_id'])
        session_list = list(df['session_id'])
        label_list = list(df['diagnosis'])

        # This dimension is for the output of image processing pipeline of Raw: 169 * 208 * 179
        if mri_plane == 0:
            self.slices_per_patient = 169 - 40
            self.slice_participant_list = [ele for ele in participant_list for _ in range(self.slices_per_patient)]
            self.slice_session_list = [ele for ele in session_list for _ in range(self.slices_per_patient)]
            self.slice_label_list = [ele for ele in label_list for _ in range(self.slices_per_patient)]
            self.slice_direction = 'sag'

        elif mri_plane == 1:
            self.slices_per_patient = 208 - 40
            self.slice_participant_list = [ele for ele in participant_list for _ in range(self.slices_per_patient)]
            self.slice_session_list = [ele for ele in session_list for _ in range(self.slices_per_patient)]
            self.slice_label_list = [ele for ele in label_list for _ in range(self.slices_per_patient)]
            self.slice_direction = 'cor'
        elif mri_plane == 2:
            self.slices_per_patient = 179 - 40
            self.slice_participant_list = [ele for ele in participant_list for _ in range(self.slices_per_patient)]
            self.slice_session_list = [ele for ele in session_list for _ in range(self.slices_per_patient)]
            self.slice_label_list = [ele for ele in label_list for _ in range(self.slices_per_patient)]
            self.slice_direction = 'axi'

    def __len__(self):
        return len(self.slice_participant_list)

    def __getitem__(self, idx):

        img_name = self.slice_participant_list[idx]
        sess_name = self.slice_session_list[idx]
        img_label = self.slice_label_list[idx]
        label = self.diagnosis_code[img_label]
        index_slice = idx % self.slices_per_patient

        # read the slices directly
        if self.transfer_learning:
            slice_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                      'preprocessing_dl',
                                      img_name + '_' + sess_name + '_space-MNI_res-1x1x1_axis-' +
                                      self.slice_direction + '_rgbslice-' + str(index_slice + 20) + '.pt')
        else:
            slice_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                      'preprocessing_dl', img_name + '_' + sess_name + '_space-MNI_res-1x1x1_axis-' +
                                      self.slice_direction + '_originalslice-' + str(index_slice + 20) + '.pt')

        extracted_slice = torch.load(slice_path)
        extracted_slice = (extracted_slice - extracted_slice.min()) / (extracted_slice.max() - extracted_slice.min())

        # check if the slice has NAN value
        if torch.isnan(extracted_slice).any():
            print("Slice %s has nan values." % str(img_name + '_' + sess_name + '_' + str(index_slice + 20)))
            extracted_slice[torch.isnan(extracted_slice)] = 0

        if self.transformations:
            extracted_slice = self.transformations(extracted_slice)

        sample = {'image_id': img_name + '_' + sess_name + '_slice' + str(index_slice + 20), 'image': extracted_slice, 'label': label}

        return sample


class MRIDataset_slice_mixed(Dataset):
    """
    This class reads the CAPS of image processing pipeline of DL. However, this is used for the bad data split strategy

    To note, this class processes the MRI to be RGB for transfer learning.

    Return: a Pytorch Dataset objective
    """

    def __init__(self, caps_directory, tsv, transformations=None, transfer_learning=False, mri_plane=0):
        """
        Args:
            caps_directory (string): the output folder of image processing pipeline.
            tsv (string): the tsv containing three columns, participant_id, session_id and diagnosis.
            transformations (callable, optional): if the data sample should be done some transformations or not, such as resize the image.

        To note, for each view:
            Axial_view = "[:, :, slice_i]"
            Coronal_veiw = "[:, slice_i, :]"
            Saggital_view= "[slice_i, :, :]"

        """
        self.caps_directory = caps_directory
        self.tsv = tsv
        self.transformations = transformations
        self.transfer_learning = transfer_learning
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1}
        self.mri_plane = mri_plane

        df = pd.read_csv(tsv, sep='\t')
        if ('diagnosis' != list(df.columns.values)[2]) and ('session_id' != list(df.columns.values)[1]) and (
            'participant_id' != list(df.columns.values)[0]) and ('slice_id' != list(df.columns.values)[1]):
            raise Exception('the data file is not in the correct format.')
        self.participant_list = list(df['participant_id'])
        self.session_list = list(df['session_id'])
        self.slice_list = list(df['slice_id'])
        self.label_list = list(df['diagnosis'])

        if mri_plane == 0:
            self.slice_direction = 'sag'

        elif mri_plane == 1:
            self.slice_direction = 'cor'
        elif mri_plane == 2:
            self.slice_direction = 'axi'

    def __len__(self):
        return len(self.participant_list)

    def __getitem__(self, idx):

        img_name = self.participant_list[idx]
        sess_name = self.session_list[idx]
        slice_name = self.slice_list[idx]
        img_label = self.label_list[idx]
        label = self.diagnosis_code[img_label]

        if self.transfer_learning:
            slice_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                      'preprocessing_dl', img_name + '_' + sess_name + '_space-MNI_res-1x1x1_axis-' +
                                      self.slice_direction + '_rgbslice-' + str(slice_name) + '.pt')
        else:
            slice_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                      'preprocessing_dl', img_name + '_' + sess_name + '_space-MNI_res-1x1x1_axis-' +
                                      self.slice_direction + '_originalslice-' + str(slice_name) + '.pt')

        extracted_slice = torch.load(slice_path)
        extracted_slice = (extracted_slice - extracted_slice.min()) / (extracted_slice.max() - extracted_slice.min())

        # check if the slice has NAN value
        if torch.isnan(extracted_slice).any():
            print("Slice %s has nan values." % str(img_name + '_' + sess_name + '_' + str(slice_name)))
            extracted_slice[torch.isnan(extracted_slice)] = 0

        if self.transformations:
            extracted_slice = self.transformations(extracted_slice)

        sample = {'image_id': img_name + '_' + sess_name + '_slice' + str(slice_name), 'image': extracted_slice, 'label': label}

        return sample

# def load_model_from_log(model, optimizer, checkpoint_dir, filename='checkpoint.pth.tar'):
#     """
#     This is to load a saved model from the log folder
#     :param model:
#     :param checkpoint_dir:
#     :param filename:
#     :return:
#     """
#     from copy import deepcopy
#
#     ## set the model to be eval mode, we explicitly think that the model was saved in eval mode, otherwise, it will affects the BN and dropout
#
#     model.eval()
#     model_updated = deepcopy(model)
#     param_dict = torch.load(os.path.join(checkpoint_dir, filename))
#     model_updated.load_state_dict(param_dict['model'])
#     optimizer.load_state_dict(param_dict['optimizer'])
#
#     return model_updated, optimizer, param_dict['global_step'], param_dict['epoch']
#
# def load_model_test(model, checkpoint_dir, filename):
#     """
#     This is to load a saved model for testing
#     :param model:
#     :param checkpoint_dir:
#     :param filename:
#     :return:
#     """
#     from copy import deepcopy
#
#     ## set the model to be eval mode, we explicitly think that the model was saved in eval mode, otherwise, it will affects the BN and dropout
#     model.eval()
#     model_updated = deepcopy(model)
#     param_dict = torch.load(os.path.join(checkpoint_dir, filename))
#     model_updated.load_state_dict(param_dict['model'])
#
#     return model_updated, param_dict['global_step'], param_dict['epoch']
#
# def hard_voting_to_tsvs(output_dir, iteration, subject_list, y_truth, y_hat, probas, mode='train', vote_mode='hard'):
#     """
#     This is a function to trace all subject during training, test and validation, and calculate the performances with different metrics into tsv files.
#     :param output_dir:
#     :param iteration:
#     :param subject_list:
#     :param y_truth:
#     :param y_hat:
#     :return:
#     """
#
#     # check if the folder exist
#     iteration_dir = os.path.join(output_dir, 'performances', 'fold_' + str(iteration))
#     if not os.path.exists(iteration_dir):
#         os.makedirs(iteration_dir)
#
#     performance_df = pd.DataFrame({'iteration': iteration,
#                                                 'y': y_truth,
#                                                 'y_hat': y_hat,
#                                                 'subject': subject_list,
#                                                 'probability': probas})
#
#     ## save the slice level results
#     performance_df.to_csv(os.path.join(iteration_dir, mode + '_slice_level_result.tsv'), index=False, sep='\t', encoding='utf-8', columns=['subject', 'y', 'y_hat', 'probability', 'iteration'])
#
#     ## save the sliece level different metrics
#     results = evaluate_prediction(list(performance_df.y), [int(e) for e in list(performance_df.y_hat)]) ## Note, y_hat here is not int, is string
#     del results['confusion_matrix']
#
#     pd.DataFrame(results, index=[0]).to_csv(os.path.join(iteration_dir, mode + '_slice_level_metrics.tsv'), index=False, sep='\t', encoding='utf-8')
#
#     ## calculate the subject-level performances based on the majority vote.
#     # delete the slice number in the column of subject
#     performance_df_subject = performance_df
#     subject_df = performance_df_subject['subject']
#     subject_series = subject_df.apply(extract_subject_name)
#     subject_df_new = pd.DataFrame({'subject': subject_series.values})
#     # replace the column in the dataframe
#     performance_df_subject['subject'] = subject_df_new['subject'].values
#
#     ## do hard majority vote
#     df_y = performance_df_subject.groupby(['subject'], as_index=False).y.mean() # get the true label for each subject
#     df_yhat = pd.DataFrame(columns=['subject', 'y_hat'])
#     for subject, subject_df in performance_df_subject.groupby(['subject']):
#         num_slice = len(subject_df.y_hat)
#         slices_predicted_as_one = subject_df.y_hat.sum()
#         if slices_predicted_as_one > num_slice / 2:
#             label = 1
#         else:
#             label = 0
#         row_array = np.array(list([subject, label])).reshape(1, 2)
#         row_df = pd.DataFrame(row_array, columns=df_yhat.columns)
#         df_yhat = df_yhat.append(row_df)
#
#     # reset the index of df_yhat
#     df_yhat.reset_index()
#     result_df = pd.merge(df_y, df_yhat, on='subject')
#     ## insert the column of iteration
#     result_df['iteration'] = str(iteration)
#
#     result_df.to_csv(os.path.join(iteration_dir, mode + '_subject_level_result_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')
#
#     results = evaluate_prediction([int(e) for e in list(result_df.y)], [int(e) for e in list(result_df.y_hat)]) ## Note, y_hat here is not int, is string
#     del results['confusion_matrix']
#
#     pd.DataFrame(results, index=[0]).to_csv(os.path.join(iteration_dir, mode + '_subject_level_metrics_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')


def extract_subject_name(s):
    return s.split('_slice')[0]


def extract_slice_index(s):
    return s.split('_slice')[1]


#################################
# Voting systems
#################################

def soft_voting_to_tsvs(output_dir, iteration, mode='test', vote_mode='soft'):
    """
    This is for soft voting for subject-level performances
    :param performance_df: the pandas dataframe, including columns: iteration, y, y_hat, subject, probability

    ref: S. Raschka. Python Machine Learning., 2015
    :return:
    """

    # check if the folder exist
    result_tsv = os.path.join(output_dir, 'performances', 'fold_' + str(iteration), mode + '_slice_level_result.tsv')

    performance_df = pd.io.parsers.read_csv(result_tsv, sep='\t')

    performance_df_subject = performance_df
    subject_df = performance_df_subject['subject']
    subject_series = subject_df.apply(extract_subject_name)
    slice_series = subject_df.apply(extract_slice_index)
    subject_df_new = pd.DataFrame({'subject': subject_series.values})
    slice_df_new = pd.DataFrame({'slice': slice_series.values})

    # replace the column in the dataframe
    performance_df_subject['subject'] = subject_df_new['subject'].values
    performance_df_subject['slice'] = slice_df_new['slice'].values

    ## selected the right classified subjects:
    right_classified_df = performance_df_subject[performance_df_subject['y_hat'] == performance_df_subject['y']]
    # right_classified_df = pd.DataFrame({'slice': right_classified_series['slice'].values})

    ## count the number of right classified slices for each slice index
    count_slices_series = right_classified_df['slice'].value_counts(normalize=True)
    index_series = performance_df_subject['slice']
    weight_list = []
    for i in index_series:
        if i in count_slices_series.index:
            weight = count_slices_series[i]
        else:
            weight = 0
        weight_list.append(weight)

    weight_series = pd.Series(weight_list)
    ## add to the df
    performance_df_subject['weight'] = weight_series.values

    ## do soft majority vote
    ## y^ = arg max(sum(wj * pij))
    df_final = pd.DataFrame(columns=['subject', 'y', 'y_hat', 'iteration'])
    for subject, subject_df in performance_df_subject.groupby(['subject']):
        num_slice = len(subject_df.y_hat)
        p0_all = 0
        p1_all = 0
        for i in range(num_slice):
            ## reindex the subject_df.probability
            proba_series_reindex = subject_df.probability.reset_index()
            weight_series_reindex = subject_df.weight.reset_index()
            y_series_reindex = subject_df.y.reset_index()
            iteration_series_reindex = subject_df.iteration.reset_index()

            p0 = weight_series_reindex.weight[i] * eval(proba_series_reindex.probability[i])[0]
            p1 = weight_series_reindex.weight[i] * eval(proba_series_reindex.probability[i])[1]

            p0_all += p0
            p1_all += p1

            if i == 0:
                y = y_series_reindex.y[i]
                iteration = iteration_series_reindex.iteration[i]
        proba_list = [p0_all, p1_all]
        y_hat = proba_list.index(max(proba_list))


        row_array = np.array(list([subject, y, y_hat, iteration])).reshape(1, 4)
        row_df = pd.DataFrame(row_array, columns=['subject', 'y', 'y_hat', 'iteration'])
        df_final = df_final.append(row_df)

    df_final.to_csv(os.path.join(os.path.join(output_dir, 'performances', 'fold_' + str(iteration), mode + '_subject_level_result_' + vote_mode + '_vote.tsv')), index=False, sep='\t', encoding='utf-8')

    results = evaluate_prediction([int(e) for e in list(df_final.y)], [int(e) for e in list(df_final.y_hat)]) ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    pd.DataFrame(results, index=[0]).to_csv(os.path.join(output_dir, 'performances', 'fold_' + str(iteration), mode + '_subject_level_metrics_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')


def soft_voting_subject_level(y_ground, y_hat, subjects, proba, iteration):
    ## soft voting to get the subject-level balanced accuracy
    performance_df_subject = pd.DataFrame({'iteration': iteration,
                                           'y': y_ground,
                                           'y_hat': y_hat,
                                           'subject': subjects,
                                           'probability': proba})

    subject_df = performance_df_subject['subject']
    subject_series = subject_df.apply(extract_subject_name)
    slice_series = subject_df.apply(extract_slice_index)
    subject_df_new = pd.DataFrame({'subject': subject_series.values})
    slice_df_new = pd.DataFrame({'slice': slice_series.values})

    # replace the column in the dataframe
    performance_df_subject['subject'] = subject_df_new['subject'].values
    performance_df_subject['slice'] = slice_df_new['slice'].values

    ## selected the right classified subjects:
    right_classified_df = performance_df_subject[performance_df_subject['y_hat'] == performance_df_subject['y']]
    # right_classified_df = pd.DataFrame({'slice': right_classified_series['slice'].values})

    ## count the number of right classified slices for each slice index
    count_slices_series = right_classified_df['slice'].value_counts(normalize=True)
    index_series = performance_df_subject['slice']
    weight_list = []
    for i in index_series:
        if i in count_slices_series.index:
            weight = count_slices_series[i]
        else:
            weight = 0
        weight_list.append(weight)

    weight_series = pd.Series(weight_list)
    ## add to the df
    performance_df_subject['weight'] = weight_series.values

    ## do soft majority vote
    ## y^ = arg max(sum(wj * pij))
    df_final = pd.DataFrame(columns=['subject', 'y', 'y_hat', 'iteration'])
    for subject, subject_df in performance_df_subject.groupby(['subject']):
        num_slice = len(subject_df.y_hat)
        p0_all = 0
        p1_all = 0
        for i in range(num_slice):
            ## reindex the subject_df.probability
            proba_series_reindex = subject_df.probability.reset_index()
            weight_series_reindex = subject_df.weight.reset_index()
            y_series_reindex = subject_df.y.reset_index()
            iteration_series_reindex = subject_df.iteration.reset_index()

            p0 = weight_series_reindex.weight[i] * proba_series_reindex.probability[i][0]
            p1 = weight_series_reindex.weight[i] * proba_series_reindex.probability[i][1]

            p0_all += p0
            p1_all += p1

            if i == 0:
                y = y_series_reindex.y[i]
                iteration = iteration_series_reindex.iteration[i]
        proba_list = [p0_all, p1_all]
        y_hat = proba_list.index(max(proba_list))

        row_array = np.array(list([subject, y, y_hat, iteration])).reshape(1, 4)
        row_df = pd.DataFrame(row_array, columns=['subject', 'y', 'y_hat', 'iteration'])
        df_final = df_final.append(row_df)

    results = evaluate_prediction([int(e) for e in list(df_final.y)], [int(e) for e in list(
        df_final.y_hat)])  ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    return results